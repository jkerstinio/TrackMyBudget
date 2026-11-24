"""Tests für bank.py mit Beispieldaten im Format der ING (MT940/MT942 über python-fints).
Aufruf: python -m pytest test_bank.py   oder   python test_bank.py
"""
import datetime as dt

from bank import assign_ids, clean_merchant, display_name, normalize, purchase_date, split_counterparty

OWN = {"DE00111111111111111111", "DE00222222222222222222"}
NAMES = {"Max Mustermann", "Max Peter Mustermann"}


def n(data, status="booked"):
    return normalize(data, iban="DE00111111111111111111", account="Persönlich", status=status, own_ibans=OWN, own_names=NAMES)


def test_card_booked():
    it = n({"date": dt.date(2026, 11, 17), "entry_date": dt.date(2026, 11, 17), "amount": "-0.66",
            "applicant_name": "VISA REWE 599 MUSTERSTA    ",
            "purpose": "NR XXXX 1111 MUSTERSTADT DEKAUFUMSATZ14.11 0.12345678ARN74000000000000000000001Google Pay",
            "posting_text": "Lastschrifteinzug", "transaction_code": "005"})
    assert it["merchant"] == "REWE" and it["key"] == "rewe"
    assert it["date"] == "2026-11-14" and it["booking_date"] == "2026-11-17"
    assert it["amount"] == -0.66 and not it["internal"]
    assert assign_ids([it])[0]["id"] == "arn:74000000000000000000001"


def test_card_pending_same_key_as_booked():
    p = n({"date": dt.date(2026, 11, 18), "entry_date": dt.date(2026, 11, 27), "amount": "-12.99",
           "applicant_name": "ALDISUED                Musterstadt",
           "purpose": "ALDI SUED MusterstadtDatum 18.11.2026 Zeit 11.20Kaufumsatz VISA CardEUR 12,99"}, status="pending")
    b = n({"date": dt.date(2026, 11, 20), "entry_date": dt.date(2026, 11, 20), "amount": "-12.99",
           "applicant_name": "VISA ALDI SUED             ", "purpose": "NR XXXX 2222 MUSTERSTADT DEKAUFUMSATZ18.11 12.99"})
    assert p["key"] == b["key"] == "aldisued" and p["merchant"] == "Aldi Süd"
    assert p["date"] == b["date"] == "2026-11-18"
    assert assign_ids([p])[0]["id"].startswith("p:")


def test_dm_both_variants():
    assert display_name(clean_merchant("VISA DM DROGERIE SAGT DANKE"))[0] == "dm"
    assert display_name(clean_merchant("DM DROGERIE SAGT DANKE   Musterstadt am"))[0] == "dm"


def test_internal_transfer_with_glued_iban():
    it = n({"date": dt.date(2026, 11, 18), "amount": "-5", "applicant_name": "DE00222222222222222222Max Peter Mustermann",
            "purpose": "Bei Beispielshop gespart.", "posting_text": "Echtzeitüberweisung", "transaction_code": "021"})
    assert it["internal"] and it["counterparty_iban"] == "DE00222222222222222222"
    assert it["key"] == "iban:DE00222222222222222222" and it["counterparty"] == "Max Peter Mustermann"


def test_credit_from_own_name_is_internal():
    it = n({"date": dt.date(2026, 11, 18), "amount": "5", "applicant_name": "Max Mustermann", "purpose": "x", "posting_text": "Gutschrift", "transaction_code": "051"})
    assert it["internal"] and it["amount"] == 5.0


def test_amazon_pending_keeps_reference():
    it = n({"date": dt.date(2026, 11, 19), "amount": "-21.99", "applicant_name": "AMAZON PAYMENTS EUROPE S.C.A.",
            "purpose": "303.1234567.7654321 AMZN Mktp DE 1AB234CDEF5GHIJK", "end_to_end_reference": "1AB234CDEF5GHIJK"}, status="pending")
    assert it["merchant"] == "Amazon" and it["end_to_end"] == "1AB234CDEF5GHIJK"


def test_year_wrap_for_card_date():
    assert purchase_date("KAUFUMSATZ30.12 1,00", dt.date(2027, 1, 2), dt.date(2027, 1, 2)) == dt.date(2026, 12, 30)


def test_duplicate_ids_get_suffix():
    a = n({"date": dt.date(2026, 11, 1), "amount": "-3", "applicant_name": "Bäcker", "purpose": "x"})
    b = dict(a)
    ids = [x["id"] for x in assign_ids([a, b])]
    assert ids[0] != ids[1] and ids[1].endswith("-1")


def test_split_without_iban():
    assert split_counterparty("Max Mustermann") == ("Max Mustermann", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
