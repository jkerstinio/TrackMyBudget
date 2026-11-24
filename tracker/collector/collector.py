"""Sammler für TrackMyBudget.

Holt per FinTS die Umsätze (gebucht + vorgemerkt) der konfigurierten ING-Girokonten und den
Depotwert, aus Gmail die PDF-Belege (TMB/Belege) und PayPal-Zahlungen (TMB/PayPal), und schreibt
alles nach <data>/bank_inbox.json. Der Tracker übernimmt die Inbox beim Öffnen. Nur lesend.

  collector.py setup     PIN und Gmail-App-Passwort im Windows-Speicher ablegen, erster Lauf mit Freigabe
  collector.py run       Abruf (für die Aufgabenplanung; fragt nichts, bricht bei nötiger Freigabe ab)
  collector.py run -i    Abruf mit Rückfragen und Freigabe in der ING-App
  collector.py status    letzten Lauf anzeigen
  collector.py forget    PIN und App-Passwort aus dem Windows-Speicher löschen
"""
from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import json
import logging
import os
import sys
import time
import tomllib
import traceback
from pathlib import Path

from bank import assign_ids, normalize

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"
LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TrackMyBudget"
STATE = LOCAL / "fints_state.bin"
LOG = LOCAL / "collector.log"
KR_FINTS = "TrackMyBudget-FinTS"
KR_GMAIL = "TrackMyBudget-Gmail"
DAYS = 35

log = logging.getLogger("collector")


class NeedInteraction(Exception):
    """Die Bank will eine Freigabe, aber der Lauf ist nicht interaktiv."""


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit(f"Konfiguration fehlt: {CONFIG} (Vorlage: config.example.toml)")
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def data_dir(cfg: dict) -> Path:
    d = Path(cfg.get("paths", {}).get("data", "../data"))
    return (HERE / d).resolve() if not d.is_absolute() else d


def secret(service: str, user: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(service, user)
    except Exception:
        return None


def stored_login() -> str | None:
    """ING-Benutzername aus dem Windows-Speicher (dort liegt er zusammen mit dem Passwort)."""
    try:
        import keyring
        c = keyring.get_credential(KR_FINTS, None)
        return c.username if c else None
    except Exception:
        return None


def write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------- FinTS
def handle_tan(client, response, what: str, interactive: bool):
    from fints.client import NeedTANResponse
    if not isinstance(response, NeedTANResponse):
        return response
    if not interactive:
        raise NeedInteraction(f"Die ING verlangt eine Freigabe ({what}).")
    print(f"\n>>> Freigabe nötig: {what}" + (f"\n    {response.challenge}" if response.challenge else ""))
    if response.decoupled:
        print("    Bitte in der ING-App bestätigen (max. 5 Minuten) …")
        deadline = time.time() + 300
        while isinstance(response, NeedTANResponse):
            if time.time() > deadline:
                raise NeedInteraction("Keine Freigabe innerhalb von 5 Minuten.")
            time.sleep(3)
            response = client.send_tan(response, "")
        print("    Freigegeben.")
        return response
    return client.send_tan(response, input("    TAN: ").strip())


def fetch_bank(cfg: dict, interactive: bool) -> dict:
    from fints.client import FinTS3PinTanClient
    from fints.utils import minimal_interactive_cli_bootstrap

    f = cfg["fints"]
    login = f.get("login") or stored_login() or (input("ING Benutzername: ").strip() if interactive else "")
    if not login:
        raise NeedInteraction("In config.toml fehlt [fints] login.")
    pin = secret(KR_FINTS, login)
    if not pin:
        if not interactive:
            raise NeedInteraction("Kein Passwort im Windows-Speicher. Einmal 'setup' ausführen.")
        pin = getpass.getpass("ING Passwort (wird nicht angezeigt): ")
    accounts_cfg: dict = cfg.get("accounts", {})  # IBAN -> Kontoname im Tracker
    own_names = set(f.get("own_names", []))

    client = FinTS3PinTanClient(f["blz"], login, pin, f["url"], product_id=f["product_id"],
                                from_data=STATE.read_bytes() if STATE.exists() else None)
    out = {"accounts": [], "items": [], "depot": None}
    start = dt.date.today() - dt.timedelta(days=DAYS)
    try:
        if interactive:
            minimal_interactive_cli_bootstrap(client)
        elif not client.get_current_tan_mechanism():
            raise NeedInteraction("TAN-Verfahren unbekannt. Einmal 'setup' ausführen.")
        with client:
            if client.init_tan_response:
                handle_tan(client, client.init_tan_response, "Anmeldung", interactive)
            sepa = handle_tan(client, client.get_sepa_accounts(), "Kontenliste", interactive)
            own_ibans = {a.iban for a in sepa if a.iban}
            items: list[dict] = []
            for acc in sepa:
                name = accounts_cfg.get(acc.iban)
                if not name:
                    continue
                bal = handle_tan(client, client.get_balance(acc), "Saldo", interactive)
                booked = handle_tan(client, client.get_transactions(acc, start), "Umsätze", interactive)
                allt = handle_tan(client, client.get_transactions(acc, start, include_pending=True), "vorgemerkte Umsätze", interactive)
                pending = allt[len(booked):]
                for t in booked:
                    items.append(normalize(t.data, iban=acc.iban, account=name, status="booked", own_ibans=own_ibans, own_names=own_names))
                for t in pending:
                    items.append(normalize(t.data, iban=acc.iban, account=name, status="pending", own_ibans=own_ibans, own_names=own_names))
                out["accounts"].append({"account": name, "iban_last4": acc.iban[-4:],
                                        "balance": float(bal.amount.amount) if bal else None,
                                        "balance_date": str(bal.date) if bal else None,
                                        "booked": len(booked), "pending": len(pending)})
                log.info("%s: %d gebucht, %d vorgemerkt", name, len(booked), len(pending))
            out["items"] = assign_ids(items)
            if cfg.get("depot", {}).get("enabled", True):
                out["depot"] = fetch_depot(client, sepa, accounts_cfg, interactive)
    finally:
        try:
            STATE.write_bytes(client.deconstruct(including_private=True))
        except Exception:
            pass
    return out


def fetch_depot(client, sepa, accounts_cfg, interactive) -> dict | None:
    """Depotbestand über alle Konten, die Wertpapiere liefern. Fehler sind hier nicht fatal."""
    positions, total, when = [], 0.0, None
    for acc in sepa:
        if acc.iban in accounts_cfg:
            continue
        try:
            hold = handle_tan(client, client.get_holdings(acc), "Depotbestand", interactive)
        except NeedInteraction:
            raise
        except Exception:
            continue
        for h in hold or []:
            value = float(h.total_value or 0)
            total += value
            when = max(when or "", str(h.valuation_date or "")[:10])
            positions.append({"name": h.name, "isin": h.ISIN, "pieces": float(h.pieces or 0), "price": float(h.market_value or 0), "value": round(value, 2)})
    if not positions:
        return None
    return {"date": when or dt.date.today().isoformat(), "value": round(total, 2), "positions": positions}


# ----------------------------------------------------------------- Gmail
def fetch_mail(cfg: dict, data: Path, ack: dict) -> dict:
    g = cfg.get("gmail", {})
    addr = g.get("address")
    pw = secret(KR_GMAIL, addr) if addr else None
    if not addr or not pw:
        return {"status": "nicht eingerichtet"}
    from mail import Gmail
    rdir = data / "inbox" / "receipts"
    rdir.mkdir(parents=True, exist_ok=True)
    inbox = data / "inbox"
    load = lambda name: json.loads((inbox / name).read_text(encoding="utf-8")) if (inbox / name).exists() else []
    store = {"paypal": load("paypal.json"), "orders": load("orders.json"), "amounts": load("mails.json")}
    seen_list = load("mail_seen.json")  # [{id, date}]
    cli = Gmail(addr, pw, g.get("queries"), int(g.get("days", 40)))
    try:
        # vom Tracker bestätigte Belege: in Gmail als erledigt markieren und lokal löschen
        done = set(ack.get("receipts_done", []))
        finished = set()
        for meta_file in rdir.glob("*.json"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta["id"] in done:
                finished.add(meta["gm_id"])
                (rdir / meta["file"]).unlink(missing_ok=True)
                meta_file.unlink(missing_ok=True)
        cli.mark_done(finished)
        # im Tracker wiederhergestellte Belege: Label TMB/erledigt entfernen, damit sie neu geladen werden
        cli.unmark_done(set(ack.get("restore", [])))
        known ={json.loads(p.read_text(encoding="utf-8"))["gm_id"] for p in rdir.glob("*.json")}
        new = cli.receipts(rdir, known)
        # dieselbe PDF in mehreren Mails (z. B. zweimal an sich selbst geschickt): nur die erste behalten
        hashes, dupes = {}, set()
        for mf in sorted(rdir.glob("*.json"), key=lambda p: json.loads(p.read_text(encoding="utf-8"))["date"]):
            meta = json.loads(mf.read_text(encoding="utf-8"))
            pdf = rdir / meta["file"]
            h = hashlib.sha1(pdf.read_bytes()).hexdigest() if pdf.exists() else None
            if h and h in hashes:
                pdf.unlink(missing_ok=True); mf.unlink(missing_ok=True); dupes.add(meta["gm_id"])
            elif h:
                hashes[h] = meta["id"]
        cli.mark_done(dupes - {json.loads(p.read_text(encoding="utf-8"))["gm_id"] for p in rdir.glob("*.json")})
        new = [m for m in new if (rdir / m["file"]).exists()]
        seen = {s["id"] for s in seen_list}
        today = dt.date.today().isoformat()
        cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
        counts = {}
        for kind in ("paypal", "orders", "amounts"):
            # auch Mails mit PDF: oft steht der Betrag im Text (z. B. Bahn), der Tracker führt beides über gm_id zusammen
            items, read = cli.scan(kind, seen)
            ids = {x["id"] for x in store[kind]}
            store[kind] = [x for x in store[kind] + [x for x in items if x["id"] not in ids] if x["date"] >= cutoff]
            seen |= read
            seen_list += [{"id": r, "date": today} for r in read]
            counts[kind] = len(items)
        for kind, name in (("paypal", "paypal.json"), ("orders", "orders.json"), ("amounts", "mails.json")):
            write_json_atomic(inbox / name, store[kind])
        write_json_atomic(inbox / "mail_seen.json", [s for s in seen_list if s["date"] >= cutoff])
    finally:
        cli.close()
    receipts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(rdir.glob("*.json"))]
    log.info("Gmail: %d neue Belege, %d PayPal, %d Bestellungen, %d Mails mit Beträgen",
             len(new), counts["paypal"], counts["orders"], counts["amounts"])
    return {"status": "ok", "receipts": receipts, "paypal": store["paypal"], "orders": store["orders"],
            "mails": store["amounts"]}


# ----------------------------------------------------------------- Ablauf
def run(interactive: bool) -> int:
    cfg = load_config()
    data = data_dir(cfg)
    if not data.exists():
        sys.exit(f"Datenordner nicht gefunden: {data}")
    inbox_path = data / "bank_inbox.json"
    prev = json.loads(inbox_path.read_text(encoding="utf-8")) if inbox_path.exists() else {}
    ack_path = data / "bank_ack.json"
    ack_list = json.loads(ack_path.read_text(encoding="utf-8")) if ack_path.exists() else []
    ack = {"receipts_done": [a["id"] for a in ack_list if a.get("kind") == "receipt_done"],
           "restore": [a["gm_id"] for a in ack_list if a.get("kind") == "receipt_restore" and a.get("gm_id")]}

    inbox = {"version": 1, "run_at": dt.datetime.now().isoformat(timespec="seconds"), "status": "ok", "message": "",
             "accounts": prev.get("accounts", []), "items": prev.get("items", []), "depot": prev.get("depot"),
             "bank_at": prev.get("bank_at"), "mail": prev.get("mail", {"status": "nicht eingerichtet"})}
    code = 0
    try:
        bank = fetch_bank(cfg, interactive)
        inbox.update(accounts=bank["accounts"], items=bank["items"], bank_at=inbox["run_at"])
        if bank["depot"]:
            inbox["depot"] = bank["depot"]
    except NeedInteraction as e:
        inbox.update(status="tan_required", message=str(e))
        log.warning("Bank: %s", e)
        code = 2
    except Exception as e:  # noqa: BLE001
        inbox.update(status="error", message=f"Bank: {e}")
        log.error("Bank-Fehler: %s\n%s", e, traceback.format_exc())
        code = 1
    try:
        inbox["mail"] = fetch_mail(cfg, data, ack)
    except Exception as e:  # noqa: BLE001
        inbox["mail"] = {**inbox.get("mail", {}), "status": f"Fehler: {e}"}
        log.error("Gmail-Fehler: %s\n%s", e, traceback.format_exc())
    write_json_atomic(inbox_path, inbox)
    msg = {0: "fertig", 1: "mit Fehler", 2: "Freigabe nötig"}[code]
    print(f"Sammler {msg}: {len(inbox['items'])} Umsätze, Belege: {len(inbox['mail'].get('receipts', []))}, "
          f"Gmail: {inbox['mail'].get('status')}" + (f"\n{inbox['message']}" if inbox["message"] else ""))
    return code


def setup():
    import keyring
    cfg = load_config()
    login = cfg["fints"].get("login") or input("ING Benutzername: ").strip()
    if not cfg["fints"].get("login"):
        print(f"Tipp: login = \"{login}\" in config.toml eintragen, dann fragt der Sammler nicht mehr.")
    if input("ING-Passwort im Windows-Speicher ablegen (nötig für den automatischen Abruf)? [j/N] ").strip().lower() == "j":
        keyring.set_password(KR_FINTS, login, getpass.getpass("ING Passwort (wird nicht angezeigt): "))
        print("Gespeichert.")
    addr = cfg.get("gmail", {}).get("address")
    if addr and input(f"Gmail-App-Passwort für {addr} ablegen? [j/N] ").strip().lower() == "j":
        keyring.set_password(KR_GMAIL, addr, getpass.getpass("App-Passwort (16 Zeichen, wird nicht angezeigt): ").replace(" ", ""))
        print("Gespeichert.")
    print("\nErster Abruf mit Freigabe in der ING-App …")
    return run(interactive=True)


def forget():
    import keyring
    cfg = load_config()
    for svc, user in ((KR_FINTS, cfg["fints"].get("login")), (KR_GMAIL, cfg.get("gmail", {}).get("address"))):
        if not user:
            continue
        try:
            keyring.delete_password(svc, user)
            print("Entfernt:", svc)
        except Exception:
            pass


def status():
    cfg = load_config()
    p = data_dir(cfg) / "bank_inbox.json"
    if not p.exists():
        print("Noch kein Lauf.")
        return
    i = json.loads(p.read_text(encoding="utf-8"))
    print(f"Letzter Lauf: {i['run_at']} · Status: {i['status']} {i.get('message', '')}")
    print(f"Bank zuletzt: {i.get('bank_at')} · {len(i['items'])} Umsätze · Gmail: {i['mail'].get('status')}")
    for a in i["accounts"]:
        print(f"  {a['account']}: Saldo {a['balance']} ({a['booked']} gebucht, {a['pending']} vorgemerkt)")
    if i.get("depot"):
        print(f"  Depot: {i['depot']['value']} € am {i['depot']['date']}")


def main():
    LOCAL.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG, encoding="utf-8")])
    logging.getLogger("fints").setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if "--wait" in sys.argv:  # beim Autostart: warten, bis das Netzwerk steht
        i = sys.argv.index("--wait")
        time.sleep(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 120)
    if cmd == "setup":
        sys.exit(setup())
    if cmd == "run":
        sys.exit(run(interactive="-i" in sys.argv))
    if cmd == "status":
        return status()
    if cmd == "forget":
        return forget()
    print(__doc__)


if __name__ == "__main__":
    main()
