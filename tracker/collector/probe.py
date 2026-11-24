"""Test-Abruf bei der ING per FinTS.

Prüft, ob die Produkt-Registrierung schon freigeschaltet ist und ob die ING
gebuchte UND vorgemerkte Umsätze liefert. Nur lesend: holt Konten, Salden und
die Umsätze der letzten 30 Tage. Speichert nichts im Tracker und überweist nichts.

Aufruf:  probe.cmd            (oder: python probe.py)
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import logging
import os
import sys
import time
import tomllib
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"
# Lokaler Ordner außerhalb von OneDrive: FinTS-Sitzungsdaten und Testausgabe
LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TrackMyBudget"
STATE = LOCAL / "fints_state.bin"
KEYRING_SERVICE = "TrackMyBudget-FinTS"
DAYS = 30


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit(f"Konfiguration fehlt: {CONFIG}\nVorlage: config.example.toml kopieren und ausfüllen.")
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["fints"]
    for key in ("product_id", "blz", "url"):
        if not cfg.get(key):
            sys.exit(f"In config.toml fehlt [fints] {key}.")
    return cfg


def ask_login(cfg: dict) -> str:
    login = (cfg.get("login") or "").strip()
    if login:
        return login
    login = input("ING Benutzername (oder 10-stellige Zugangsnummer, falls du nie einen Benutzernamen angelegt hast): ").strip()
    if not login:
        sys.exit("Ohne Benutzername/Zugangsnummer geht es nicht.")
    return login


def ask_pin(login: str) -> str:
    try:
        import keyring
        pin = keyring.get_password(KEYRING_SERVICE, login)
        if pin:
            print("PIN aus dem Windows-Anmeldeinformationsspeicher verwendet.")
            return pin
    except Exception:
        keyring = None
    pin = getpass.getpass("ING Passwort (bzw. alte Internetbanking-PIN), wird nicht angezeigt: ")
    if keyring and input("PIN im Windows-Speicher merken? [j/N] ").strip().lower() == "j":
        keyring.set_password(KEYRING_SERVICE, login, pin)
        print("Gespeichert. Entfernen mit: python probe.py --forget")
    return pin


def handle_tan(client, response, what: str):
    """Wartet auf die Freigabe in der ING-App (decoupled) oder fragt eine TAN ab."""
    from fints.client import NeedTANResponse

    if not isinstance(response, NeedTANResponse):
        return response
    print(f"\n>>> Die ING möchte eine Freigabe für: {what}")
    if response.challenge:
        print("    Hinweis der Bank:", response.challenge)
    if response.decoupled:
        print("    Bitte jetzt in der ING-App bestätigen. Ich warte bis zu 5 Minuten …")
        deadline = time.time() + 300
        while isinstance(response, NeedTANResponse):
            if time.time() > deadline:
                sys.exit("Keine Freigabe innerhalb von 5 Minuten. Abbruch.")
            time.sleep(3)
            response = client.send_tan(response, "")
        print("    Freigegeben.")
        return response
    tan = input("    TAN eingeben: ").strip()
    return client.send_tan(response, tan)


def mask(iban: str | None) -> str:
    return f"{iban[:4]} … {iban[-4:]}" if iban else "?"


def short(d: dict) -> dict:
    """Die für uns interessanten Felder eines Umsatzes (Feldnamen wie python-fints sie liefert)."""
    keys = ["date", "entry_date", "guessed_entry_date", "amount", "currency", "status",
            "applicant_name", "applicant_iban", "purpose", "posting_text", "transaction_code",
            "end_to_end_reference", "customer_reference", "bank_reference", "id"]
    out = {k: d[k] for k in keys if k in d and d[k] not in (None, "")}
    return out or d


def tx_dict(t) -> dict:
    data = getattr(t, "data", t)
    return {k: (str(v) if not isinstance(v, (str, int, float, bool, type(None), dict, list)) else v)
            for k, v in dict(data).items()}


def main():
    if "--forget" in sys.argv:
        import keyring
        login = ask_login(load_config())
        try:
            keyring.delete_password(KEYRING_SERVICE, login)
            print("PIN aus dem Windows-Speicher entfernt.")
        except Exception:
            print("Es war keine PIN gespeichert.")
        return

    logging.basicConfig(level=logging.WARNING, format="  [fints] %(message)s")
    from fints.client import FinTS3PinTanClient, FinTSUnsupportedOperation, NeedTANResponse
    from fints.camt_parser import camt053_to_dict
    from fints.utils import minimal_interactive_cli_bootstrap

    cfg = load_config()
    login = ask_login(cfg)
    pin = ask_pin(login)
    LOCAL.mkdir(parents=True, exist_ok=True)

    bank_msgs: list[str] = []

    def on_response(segment, response):
        line = f"{response.code} {response.text}"
        if line not in bank_msgs:
            bank_msgs.append(line)

    client = FinTS3PinTanClient(
        cfg["blz"], login, pin, cfg["url"],
        product_id=cfg["product_id"],
        from_data=STATE.read_bytes() if STATE.exists() else None,
    )
    client.add_response_callback(on_response)

    report = {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "accounts": []}
    start = dt.date.today() - dt.timedelta(days=DAYS)
    print(f"\nVerbinde mit {cfg['url']} …")
    try:
        minimal_interactive_cli_bootstrap(client)
        with client:
            if client.init_tan_response:
                handle_tan(client, client.init_tan_response, "Anmeldung")

            accounts = handle_tan(client, client.get_sepa_accounts(), "Kontenliste")
            print(f"\n{len(accounts)} Konto/Konten gefunden.")
            for acc in accounts:
                print(f"\n=== Konto {mask(acc.iban)} ===")
                entry = {"iban_masked": mask(acc.iban), "iban_last4": (acc.iban or "")[-4:]}

                try:
                    bal = handle_tan(client, client.get_balance(acc), "Saldo")
                    entry["balance"] = str(bal.amount) if bal else None
                    print("  Saldo:", entry["balance"])
                except Exception as e:  # noqa: BLE001
                    entry["balance_error"] = repr(e)
                    print("  Saldo nicht abrufbar:", e)

                # Weg 1: camt (HKCAZ) liefert gebucht und vorgemerkt getrennt
                try:
                    res = handle_tan(client, client.get_transactions_xml(acc, start), "Umsätze (camt)")
                    booked_streams, pending_streams = res
                    booked = [x for s in booked_streams for x in camt053_to_dict(s)]
                    pending = [x for s in pending_streams if s for x in camt053_to_dict(s)]
                    entry["camt"] = {"booked": len(booked), "pending": len(pending),
                                     "booked_sample": [short(x) for x in booked[:3]],
                                     "pending_sample": [short(x) for x in pending[:3]]}
                    print(f"  camt:  {len(booked)} gebucht, {len(pending)} vorgemerkt")
                except FinTSUnsupportedOperation:
                    entry["camt"] = "nicht unterstützt"
                    print("  camt:  von der Bank nicht unterstützt")
                except Exception as e:  # noqa: BLE001
                    entry["camt_error"] = repr(e)
                    print("  camt:  Fehler:", e)

                # Weg 2: MT940/MT942 (HKKAZ), vorgemerkte kommen als MT942 hinterher
                try:
                    only_booked = handle_tan(client, client.get_transactions(acc, start), "Umsätze (MT940)")
                    with_pending = handle_tan(client, client.get_transactions(acc, start, include_pending=True), "Umsätze inkl. vorgemerkt (MT940)")
                    nb, np_ = len(only_booked), len(with_pending) - len(only_booked)
                    entry["mt940"] = {"booked": nb, "pending": np_,
                                      "booked_sample": [short(tx_dict(x)) for x in only_booked[-3:]],
                                      "pending_sample": [short(tx_dict(x)) for x in with_pending[nb:nb + 3]]}
                    print(f"  MT940: {nb} gebucht, {np_} vorgemerkt")
                except FinTSUnsupportedOperation:
                    entry["mt940"] = "nicht unterstützt"
                    print("  MT940: von der Bank nicht unterstützt")
                except Exception as e:  # noqa: BLE001
                    entry["mt940_error"] = repr(e)
                    print("  MT940: Fehler:", e)

                report["accounts"].append(entry)
        # Sitzungsdaten sichern: spätere Abrufe brauchen dann seltener eine Freigabe
        STATE.write_bytes(client.deconstruct(including_private=True))
    except Exception as e:  # noqa: BLE001
        report["error"] = repr(e)
        print("\nABBRUCH:", e)
    finally:
        report["bank_messages"] = bank_msgs
        out = LOCAL / f"probe-{dt.date.today():%Y%m%d}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\nMeldungen der Bank:")
    for m in bank_msgs:
        print("  ", m)
    if any(m.startswith(("9078", "9210", "9800")) or "Produkt" in m or "registr" in m.lower() for m in bank_msgs):
        print("\n  Hinweis: Wenn hier etwas zur Produkt-Registrierung steht, ist die Nummer bei der ING\n"
              "  noch nicht freigeschaltet. Das ist laut Registrierungsstelle normal, in ein paar Tagen erneut testen.")
    print(f"\nErgebnis gespeichert (nur lokal): {out}")
    print("Sag Claude Bescheid, dann wertet es die Datei aus.")


if __name__ == "__main__":
    main()
