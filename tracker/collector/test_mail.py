"""Tests für den PayPal-Parser (Texte nach echten PayPal-Mails, gekürzt)."""
import datetime as dt
from mail import parse_paypal

D = dt.date(2026, 11, 18)

def test_paid():
    p = parse_paypal("Beleg für Ihre Zahlung an Beispielshop", "Hallo Max!\nSie haben 30,98 € EUR an Beispielshop gezahlt\nHändler\nBeispielshop\nTransaktionsdatum\n18.11.2026\n", D)
    assert p == {"merchant": "Beispielshop", "amount": -30.98, "date": "2026-11-18", "direction": "out"}

def test_authorized_long_merchant():
    p = parse_paypal("Muster Handels GmbH &...: 35,98 € EUR", "Sie haben 35,98 € EUR an Muster Handels GmbH &... autorisiert\nHändler\nMuster Handels GmbH & Co. KG\n", dt.date(2026, 11, 14))
    assert p["amount"] == -35.98 and p["merchant"] == "Muster Handels GmbH & Co. KG" and p["date"] == "2026-11-14"

def test_received():
    p = parse_paypal("Du hast eine Zahlung erhalten", "Du hast 36,00 € EUR erhalten Hallo Max Peter Mustermann! Erika Musterfrau hat dir 36,00 € EUR gesendet Transaktionsdatum 18.11.2026", D)
    assert p["direction"] == "in" and p["amount"] == 36.0 and p["merchant"] == "Erika Musterfrau"

def test_unknown():
    assert parse_paypal("Neue Funktionen bei PayPal", "Entdecke jetzt ...", D) is None

from mail import parse_amazon, parse_amounts

AMZ = """Bestellnr.
303-1234567-7654321

Zustellung: Freitag

* 6 Stück LED Teelichter Wiederaufladbare mit Fernbedienung | Dimmbar Kerzen
  Menge: 1
  21.99 EUR

Zustellung: Dienstag

* Bunte Socken Damen, Baumwolle
  Menge: 1
  11.99 EUR

* Handcreme Rosmarin, vegan, 75 ml
  Menge: 2
  2.95 EUR

Summe
43.92 EUR
"""

def test_amazon():
    o = parse_amazon("Bestellt: x", AMZ, D)
    assert o["order"] == "303-1234567-7654321" and o["total"] == -43.92
    assert [len(s) for s in o["shipments"]] == [1, 2]
    assert o["shipments"][0][0] == {"name": "6 Stück LED Teelichter Wiederaufladbare mit Fernbedienung", "qty": 1, "price": 21.99}
    assert o["shipments"][1][1]["qty"] == 2 and o["shipments"][1][1]["price"] == 2.95

def test_amounts_total():
    m = parse_amounts("Ihre Rechnung Nr. 4711", "Artikel A 10,00 €\nVersand 4,90 €\nZwischensumme 14,90 €\nGesamtbetrag:\n 14,90 €\n", D, '"Shop GmbH" <noreply@shop.de>')
    assert m["total"] == 14.90 and m["total_sure"] and m["merchant"] == "Shop GmbH" and 4.90 in m["amounts"]

def test_amounts_eur_before_and_dot():
    m = parse_amounts("Buchungsbestätigung", "Ticketpreis EUR 29.90 inkl. MwSt.", D, "tickets@bahn.de")
    assert m["total"] == 29.90 and not m["total_sure"] and m["merchant"] == "bahn.de"

def test_amounts_none():
    assert parse_amounts("Ihre Bestellung ist unterwegs", "Sendungsnummer 123", D, "a@b.de") is None

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
