"""Umsätze aus python-fints (MT940/MT942) in das Inbox-Format des Trackers bringen.

Reine Funktionen ohne Netzwerk, damit sie testbar sind (siehe test_bank.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal

IBAN_PREFIX = re.compile(r"^([A-Z]{2}\d{2}[0-9]{8,30})\s*")
CARD_BOOKED_DATE = re.compile(r"KAUFUMSATZ\s*(\d{2})\.(\d{2})", re.I)
CARD_PENDING_DATE = re.compile(r"Datum\s+(\d{2})\.(\d{2})\.(\d{4})", re.I)
ARN = re.compile(r"ARN(\d{10,})")

# Anzeige-Namen für bekannte Händler. Schlüssel = Anfang des normalisierten Namens.
BRANDS = [
    ("rewe", "REWE"), ("aldisued", "Aldi Süd"), ("aldi", "Aldi"), ("lidl", "Lidl"), ("kaufland", "Kaufland"),
    ("edeka", "Edeka"), ("penny", "Penny"), ("netto", "Netto"), ("tegut", "Tegut"), ("alnatura", "Alnatura"),
    ("dmdrogerie", "dm"), ("dmfil", "dm"), ("rossmann", "Rossmann"), ("mueller", "Müller"),
    ("amazon", "Amazon"), ("amzn", "Amazon"), ("paypal", "PayPal"), ("spotify", "Spotify"), ("netflix", "Netflix"),
    ("apple", "Apple"), ("google", "Google"), ("dbvertrieb", "Deutsche Bahn"), ("deutschebahn", "Deutsche Bahn"),
    ("ikea", "IKEA"), ("decathlon", "Decathlon"), ("shell", "Shell"), ("aral", "Aral"), ("esso", "Esso"),
    ("lieferando", "Lieferando"), ("dominos", "Domino's"), ("mcdonalds", "McDonald's"), ("vattenfall", "Vattenfall"),
    ("vodafone", "Vodafone"), ("telekom", "Telekom"), ("rundfunk", "Rundfunkbeitrag"),
]


def norm_key(s: str) -> str:
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z]", "", s)


def split_counterparty(name: str) -> tuple[str, str | None]:
    """ING klebt bei Überweisungen die IBAN vor den Namen: 'DE91…1234Max Mustermann'."""
    name = (name or "").strip()
    m = IBAN_PREFIX.match(name)
    if m:
        return name[m.end():].strip(), m.group(1)
    return name, None


def clean_merchant(name: str) -> str:
    """'VISA REWE 599 MUSTERSTA' -> 'REWE', 'ALDISUED      Musterstadt' -> 'ALDISUED'."""
    n = re.sub(r"^VISA\s+", "", name.strip(), flags=re.I)
    n = re.split(r"\s{2,}", n)[0]                  # Ort nach mehreren Leerzeichen abschneiden
    n = re.split(r"\s+\d", n)[0]                   # alles ab Filialnummer abschneiden
    return n.strip(" .,-")


def display_name(cleaned: str) -> tuple[str, str]:
    """Anzeige-Name und stabiler Regel-Schlüssel."""
    key = norm_key(cleaned)
    for prefix, label in BRANDS:
        if key.startswith(prefix):
            return label, prefix
    if not cleaned:
        return "", ""
    pretty = cleaned if not cleaned.isupper() else cleaned.title()
    return pretty, key


def purchase_date(purpose: str, value_date: dt.date, booking_date: dt.date) -> dt.date:
    """Echtes Kaufdatum aus dem Verwendungszweck (Karte), sonst Wertstellung."""
    m = CARD_PENDING_DATE.search(purpose or "")
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = CARD_BOOKED_DATE.search(purpose or "")
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = booking_date.year - (1 if month > booking_date.month else 0)
        try:
            return dt.date(year, month, day)
        except ValueError:
            pass
    return value_date


def _amount(a) -> float:
    if hasattr(a, "amount"):
        a = a.amount
    return float(Decimal(str(a)))


def _date(d) -> dt.date:
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d)[:10])


def normalize(data: dict, *, iban: str, account: str, status: str, own_ibans: set[str], own_names: set[str]) -> dict:
    """Ein MT940-Umsatz (Transaction.data) -> Inbox-Eintrag."""
    raw_name = data.get("applicant_name") or ""
    name, cp_iban = split_counterparty(raw_name)
    cp_iban = cp_iban or (data.get("applicant_iban") or None)
    purpose = re.sub(r"\s+", " ", (data.get("purpose") or "")).strip()
    value_date = _date(data.get("date"))
    booking_date = _date(data.get("entry_date") or data.get("date"))
    amount = round(_amount(data.get("amount")), 2)
    cleaned = clean_merchant(name) if name.strip() else ""
    merchant, key = display_name(cleaned)
    if not merchant:  # z. B. Wertpapierkauf ohne Gegenseite
        merchant = (data.get("posting_text") or "Umsatz").strip()
        key = "pt:" + norm_key(merchant)
    internal = bool(cp_iban and cp_iban in own_ibans) or norm_key(name) in {norm_key(n) for n in own_names}
    if cp_iban and not key.startswith("pt:") and (internal or data.get("transaction_code") in ("020", "021", "051", "052", "166")):
        key = "iban:" + cp_iban  # Überweisungen: Regel an die IBAN der Gegenseite
    return {
        "status": status,
        "account": account,
        "iban_last4": iban[-4:],
        "date": purchase_date(purpose, value_date, booking_date).isoformat(),
        "booking_date": booking_date.isoformat(),
        "amount": amount,
        "merchant": merchant,
        "key": key,
        "counterparty": name,
        "counterparty_iban": cp_iban,
        "purpose": purpose,
        "posting_text": (data.get("posting_text") or "").strip(),
        "end_to_end": (data.get("end_to_end_reference") or "") or None,
        "internal": internal,
    }


def assign_ids(items: list[dict]) -> list[dict]:
    """Stabile Kennung je Umsatz. Gebuchte Kartenzahlungen haben eine ARN, sonst ein Hash.
    Gleiche Umsätze am selben Tag bekommen eine laufende Nummer."""
    seen: dict[str, int] = {}
    for it in items:
        m = ARN.search(it["purpose"] or "")
        if it["status"] == "booked" and m:
            base = "arn:" + m.group(1)
        else:
            # vorgemerkt: ohne booking_date, das ist nur das voraussichtliche Buchungsdatum und ändert sich von Abruf zu Abruf
            keys = ("status", "iban_last4", "date", "amount", "counterparty", "purpose") if it["status"] == "pending" else \
                   ("status", "iban_last4", "booking_date", "date", "amount", "counterparty", "purpose")
            raw = "|".join(str(it[k]) for k in keys)
            base = ("p:" if it["status"] == "pending" else "b:") + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        n = seen.get(base, 0)
        seen[base] = n + 1
        it["id"] = base if n == 0 else f"{base}-{n}"
    return items
