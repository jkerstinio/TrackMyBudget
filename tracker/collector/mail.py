"""Gmail per IMAP: PDF-Belege, PayPal-Zahlungen, Amazon-Bestellungen und alle Mails mit Euro-Beträgen.

Keine Labels oder Filter nötig: der Sammler sucht mit Gmail-Suchanfragen (aus config.toml überschreibbar)
in "Alle Nachrichten". Werbung, soziale Netzwerke und gesendete Mails werden ausgelassen.
Schon gelesene Mails merkt sich der Sammler lokal (mail_seen.json); nur zugeordnete Belege bekommen
in Gmail das Label TMB/erledigt.
"""
from __future__ import annotations

import datetime as dt
import email
import email.header
import email.utils
import imaplib
import json
import re
from email.message import Message
from pathlib import Path

HOST = "imap.gmail.com"
DONE_LABEL = '"TMB/erledigt"'
NOISE = "-category:promotions -category:social -in:sent -in:chats"
QUERIES = {
    # PDF-Anhänge, die nach Rechnung oder Kassenbon aussehen
    # an sich selbst geschickte PDFs zählen immer (Betreff egal); nur von dir an andere Gesendetes nicht
    "receipts": "has:attachment filename:pdf -category:promotions -category:social -in:chats -label:TMB-erledigt "
                "-(from:me -to:me) "
                "{rechnung invoice beleg quittung ebon kassenbon receipt bestellung label:TMB-Belege from:rewe "
                "(from:me to:me)}",
    "paypal": "from:service@paypal.de",
    "orders": "from:bestellbestaetigung@amazon.de",
    # alle übrigen Mails, die nach Kauf, Rechnung oder Zahlung aussehen
    "amounts": NOISE + " -from:paypal.de -from:amazon.de -from:trustpilotmail.com -from:medallia.eu "
               "-subject:{geliefert zugestellt versendet unterwegs verpackt meinung bewerte bewertung} "
               "{rechnung bestellung bestellbestaetigung bestellbestätigung zahlung zahlungsbestaetigung quittung beleg "
               "kaufbeleg buchungsbestaetigung buchung abbuchung lastschrift order invoice receipt payment ticket}",
}


def _dec(s: str | None) -> str:
    if not s:
        return ""
    return str(email.header.make_header(email.header.decode_header(s)))


def _text(msg: Message) -> str:
    """Klartext der Mail; HTML wird grob in Text umgewandelt."""
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if part.get_content_type() == "text/plain" else html if part.get_content_type() == "text/html" else []).append(txt)
    if plain and any(re.search(r"\d,\d\d|\d\.\d\d", p) for p in plain):
        return "\n".join(plain)
    h = "\n".join(html) or "\n".join(plain)
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|td|li|h\d)>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&euro;", "€").replace("&#8364;", "€")
    return re.sub(r"[ \t ]+", " ", h)


AMOUNT = r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€"
NUM = r"(\d{1,3}(?:[.  ]\d{3})*,\d{2}|\d+\.\d{2})"
EUR_AFTER = re.compile(NUM + r"\s*(?:€|EUR\b)")
EUR_BEFORE = re.compile(r"(?:€|EUR)\s*" + NUM)
TOTAL = re.compile(r"(?i)(gesamtbetrag|gesamtsumme|rechnungsbetrag|endbetrag|gesamtpreis|zu zahlen|zahlbetrag|"
                   r"bezahlter betrag|abgebuchter betrag|gesamt|summe|total|betrag)[^\d€\n]{0,40}\n?[^\d€\n]{0,20}"
                   r"(?:(?:€|EUR)\s*" + NUM + r"|" + NUM + r"\s*(?:€|EUR\b))")


def _num(s: str) -> float:
    s = s.replace(" ", "").replace(" ", "")
    return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)


def parse_amounts(subject: str, body: str, date: dt.date, sender: str) -> dict | None:
    """Beliebige Mail -> {merchant, total, amounts, date, subject}, wenn Euro-Beträge darin stehen."""
    text = subject + "\n" + body
    found = [_num(m.group(1)) for m in EUR_AFTER.finditer(text)] + [_num(m.group(1)) for m in EUR_BEFORE.finditer(text)]
    found = [a for a in found if 0 < a < 100000]
    if not found:
        return None
    total = None
    tm = [m for m in TOTAL.finditer(text)]
    if tm:  # letzte Gesamt-Zeile gewinnt (Zwischensummen stehen meist davor)
        g = tm[-1]
        total = _num(g.group(2) or g.group(3))
    amounts = sorted(set(found), key=lambda a: (-found.count(a), -a))[:12]
    name = email.utils.parseaddr(sender)[0] or email.utils.parseaddr(sender)[1].split("@")[-1]
    return {"merchant": name.strip(' "'), "total": total if total is not None else max(found), "total_sure": total is not None,
            "amounts": amounts, "date": date.isoformat(), "subject": subject[:140]}


def parse_paypal(subject: str, body: str, date: dt.date) -> dict | None:
    """PayPal-Zahlungsbestätigung -> {merchant, amount, date, direction}. Unbekannte Mails -> None."""
    text = subject + "\n" + body
    # Ausgehend: "Sie haben 30,98 € EUR an Beispielshop gezahlt" / "... an X autorisiert" / Betreff "Beleg für Ihre Zahlung an X"
    m = re.search(r"(?:haben|hast)\s+" + AMOUNT + r"\s*EUR\s+an\s+(.+?)\s+(?:gezahlt|gesendet|autorisiert)", text)
    direction = "out"
    if not m:
        m = re.search(r"(?:haben|hast)\s+" + AMOUNT + r"\s*EUR\s+(?:von\s+(.+?)\s+)?erhalten", text)
        direction = "in"
    if not m:
        return None
    amount = float(m.group(1).replace(".", "").replace(",", "."))
    merchant = (m.group(2) or "").strip(" .&")
    h = re.search(r"Händler\s*\n?\s*([^\n]+)", body)
    if direction == "out" and h and len(h.group(1).strip()) > len(merchant):
        merchant = h.group(1).strip()
    if direction == "in" and not merchant:
        s = re.search(r"([A-ZÄÖÜ][\wäöüß\-]+(?:[ \t]+[A-ZÄÖÜ][\wäöüß\-]+){0,3})[ \t]+hat dir[ \t]+" + AMOUNT, text)
        merchant = s.group(1).strip() if s else "PayPal"
    td = re.search(r"Transaktionsdatum\s*\n?\s*(\d{2})\.(\d{2})\.(\d{4})", body)
    if td:
        date = dt.date(int(td.group(3)), int(td.group(2)), int(td.group(1)))
    return {"merchant": merchant.replace("…", "").strip(), "amount": -amount if direction == "out" else amount,
            "date": date.isoformat(), "direction": direction}


def parse_amazon(subject: str, body: str, date: dt.date) -> dict | None:
    """Amazon-Bestellbestätigung -> {order, total, date, shipments: [[{name, qty, price}]]}."""
    o = re.search(r"Bestellnr\.?\s*\n?\s*(\d{3}-\d{7}-\d{7})", body)
    t = re.search(r"Summe\s*\n?\s*" + NUM + r"\s*EUR", body)
    if not o or not t:
        return None
    shipments: list[list[dict]] = []
    for line in body.splitlines():
        if re.match(r"\s*Zustellung:", line):
            shipments.append([])
            continue
        m = re.match(r"\s*\* (.+)", line)
        if m:
            if not shipments:
                shipments.append([])
            shipments[-1].append({"name": re.split(r"\s+[|,]\s+", m.group(1).strip())[0][:80], "qty": 1, "price": None})
            continue
        last = shipments[-1][-1] if shipments and shipments[-1] else None
        q = re.match(r"\s*Menge:\s*(\d+)", line)
        pr = re.match(r"\s*" + NUM + r"\s*EUR\s*$", line)
        if last and q:
            last["qty"] = int(q.group(1))
        elif last and pr and last["price"] is None:
            last["price"] = _num(pr.group(1))
    return {"order": o.group(1), "total": -_num(t.group(1)), "date": date.isoformat(),
            "shipments": [s for s in shipments if s]}


def _quote(query: str) -> str:
    return '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Gmail:
    def __init__(self, address: str, app_password: str, queries: dict | None = None, days: int = 40):
        self.imap = imaplib.IMAP4_SSL(HOST)
        self.imap.login(address, app_password)
        self.queries = {**QUERIES, **(queries or {})}
        self.days = days
        typ, boxes = self.imap.list()
        allm = next((b for b in boxes or [] if b and b"\\All" in b), None)
        if not allm:
            raise RuntimeError("Ordner 'Alle Nachrichten' nicht gefunden (in Gmail unter IMAP sichtbar?)")
        self.all_box = re.search(rb'"([^"]+)"\s*$', allm).group(1).decode()
        typ, _ = self.imap.select(f'"{self.all_box}"', readonly=False)
        if typ != "OK":
            raise RuntimeError(f"Ordner {self.all_box} lässt sich nicht öffnen")

    def close(self):
        try:
            self.imap.logout()
        except Exception:
            pass

    def search(self, kind: str) -> list[tuple[bytes, str]]:
        """Treffer der Suchanfrage als (uid, Gmail-Mail-ID)."""
        q = f"{self.queries[kind]} newer_than:{self.days}d"
        enc = q.encode("utf-8")
        if all(b < 128 for b in enc):
            typ, data = self.imap.uid("SEARCH", None, "X-GM-RAW", _quote(q))
        else:  # Umlaute: als UTF-8-Literal senden
            self.imap.literal = enc
            typ, data = self.imap.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        if typ != "OK":
            raise RuntimeError(f"Gmail-Suche fehlgeschlagen: {q}")
        uids = data[0].split() if data and data[0] else []
        if not uids:
            return []
        typ, data = self.imap.uid("FETCH", b",".join(uids).decode(), "(X-GM-MSGID)")
        out = []
        for line in data or []:
            line = line[0] if isinstance(line, tuple) else line
            m = re.search(rb"X-GM-MSGID (\d+).*UID (\d+)|UID (\d+).*X-GM-MSGID (\d+)", line or b"")
            if m:
                gid, uid = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
                out.append((uid, gid.decode()))
        return out

    def _fetch(self, uid: bytes) -> Message:
        typ, data = self.imap.uid("FETCH", uid, "(RFC822)")
        raw = next(x for x in data if isinstance(x, tuple))
        return email.message_from_bytes(raw[1])

    @staticmethod
    def _date(msg: Message) -> dt.date:
        try:
            return email.utils.parsedate_to_datetime(msg["Date"]).date()
        except Exception:
            return dt.date.today()

    def mark_done(self, gm_ids: set[str]):
        for gid in gm_ids:
            typ, data = self.imap.uid("SEARCH", None, f"X-GM-MSGID {gid}")
            for uid in (data[0].split() if typ == "OK" and data and data[0] else []):
                self.imap.uid("STORE", uid, "+X-GM-LABELS", f"({DONE_LABEL})")

    def unmark_done(self, gm_ids: set[str]):
        for gid in gm_ids:
            typ, data = self.imap.uid("SEARCH", None, f"X-GM-MSGID {gid}")
            for uid in (data[0].split() if typ == "OK" and data and data[0] else []):
                self.imap.uid("STORE", uid, "-X-GM-LABELS", f"({DONE_LABEL})")

    def receipts(self, folder: Path, known: set[str]) -> list[dict]:
        """Neue PDF-Anhänge nach folder speichern. Gibt Metadaten zurück."""
        out = []
        for uid, gid in self.search("receipts"):
            if gid in known:
                continue
            msg = self._fetch(uid)
            date = self._date(msg)
            n = 0
            for part in msg.walk():
                fn = _dec(part.get_filename())
                if not fn or not (fn.lower().endswith(".pdf") or part.get_content_type() == "application/pdf"):
                    continue
                n += 1
                rid = gid if n == 1 else f"{gid}-{n}"
                safe = re.sub(r"[^\w.\-]+", "_", fn)[-80:]
                (folder / f"{rid}_{safe}").write_bytes(part.get_payload(decode=True))
                meta = {"id": rid, "gm_id": gid, "file": f"{rid}_{safe}", "date": date.isoformat(),
                        "from": _dec(msg["From"]), "subject": _dec(msg["Subject"])}
                (folder / f"{rid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                out.append(meta)
        return out

    def scan(self, kind: str, seen: set[str], skip: set[str] = frozenset()) -> tuple[list[dict], set[str]]:
        """paypal / orders / amounts: neue Mails lesen. Gibt Einträge und die gelesenen Mail-IDs zurück."""
        out, read = [], set()
        for uid, gid in self.search(kind):
            if gid in seen or gid in skip:
                continue
            msg = self._fetch(uid)
            date, subj, body = self._date(msg), _dec(msg["Subject"]), _text(msg)
            if kind == "paypal":
                p = parse_paypal(subj, body, date)
                if p:
                    p["id"] = "pp:" + gid
            elif kind == "orders":
                p = parse_amazon(subj, body, date)
                if p:
                    p.update(id="az:" + p["order"], merchant="Amazon", gm_id=gid)
            else:
                p = parse_amounts(subj, body, date, _dec(msg["From"]))
                if p:
                    p.update(id="m:" + gid, gm_id=gid)
            if p:
                out.append(p)
            read.add(gid)
        return out, read
