# Automatische Erfassung

Quelle der Wahrheit ist die **ING**. Der Sammler (`tracker/collector`) holt täglich per FinTS die gebuchten **und vorgemerkten** Umsätze der Girokonten Persönlich und Gemeinsam sowie den Depotwert. Aus Gmail kommen Zusatzinfos: PDF-Belege und Rechnungen, PayPal-Zahlungen (echter Händler), Amazon-Bestellungen mit Artikeln und alle übrigen Kauf-, Rechnungs- und Zahlungsmails mit Euro-Beträgen. Der Sammler sucht dafür selbst mit Gmail-Suchen; Labels oder Filter braucht es nicht. MacroDroid wird nicht mehr gebraucht.

Der Tracker übernimmt alles auf der Seite **„Neue Umsätze“**.

## Ablauf je Umsatz

| Reihenfolge | Fall | Ergebnis |
|---|---|---|
| 1 | schon verknüpft | Betrag/Status werden aktualisiert |
| 2 | gebucht, passende vorgemerkte Buchung (Konto, Betrag, ±4 Tage) | wird zu „gebucht“, Kategorie bleibt |
| 3 | von Hand erfasst (Konto, Betrag, ±3 Tage) | wird verknüpft statt doppelt angelegt |
| 4 | passt zu fälliger Wiederkehrend-Buchung (±1 %, −10…+6 Tage) | automatisch gebucht, Fälligkeit rückt weiter |
| 5 | bekannte Gegenseite (gelernte Regel) | automatisch gebucht → Liste „Automatisch gebucht“ |
| 6 | sonst | „Neue Umsätze“: Kategorie antippen, wird für die Gegenseite gemerkt |

- **PayPal:** Abbuchung „PayPal“ + PayPal-Mail mit gleichem Betrag (±5 Tage) → Händler aus der Mail.
- **Umbuchungen** zwischen eigenen Konten sind markiert. „Umbuchung ignorieren“ merkt sich das für diese IBAN.
- **Kassenbon:** „Zuordnen“ sucht die Bankbuchung mit gleichem Betrag (±4 Tage) und teilt sie nach Kategorien auf (Pfand getrennt). Die Bankbuchung bleibt verknüpft.
- **Vorgemerkt, nie gebucht:** eigene Liste zum Löschen oder Behalten.
- **Depot:** neuer Depotwert von der ING mit einem Klick übernehmen (Rendite wird gebucht).

## Einrichtung

1. **Gmail**: nur ein App-Passwort unter myaccount.google.com/apppasswords erzeugen und mit `einrichten.cmd` ablegen. Belege, die nicht von selbst gefunden werden (z. B. Kaufland, dm-App), an sich selbst schicken mit „Beleg“ im Betreff oder mit dem Label `TMB/Belege` versehen.
2. `tracker/collector/config.toml` prüfen (Produkt-ID, Konten, Gmail-Adresse; optional `login` = ING-Benutzername).
3. **`einrichten.cmd`** starten: ING-Passwort und Gmail-App-Passwort im Windows-Speicher ablegen, erster Abruf mit Freigabe in der ING-App.
4. Tracker öffnen → „Neue Umsätze“ → **Übernahme-Datum** wählen (Vorschlag: Tag nach der letzten eigenen Buchung; überlappende Handeinträge werden verknüpft).
5. **`aufgabe_anlegen.cmd`** starten: täglicher Abruf um 7:00 und bei der Anmeldung, ohne Fenster.

## Im Alltag

- Tracker öffnen, „Neue Umsätze“ abarbeiten. Die Seite liest `bank_inbox.json` beim Öffnen, beim Zurückkehren ins Fenster und alle 2 Minuten neu.
- Meldet der Tracker **„Die ING möchte eine Freigabe“** (meist alle 90 Tage): `abrufen.cmd` starten und in der ING-App bestätigen.
- **Aufteilen:** Bei jeder ING-Buchung (Neue Umsätze, Übernommen, Transaktionsliste, Bearbeiten) gibt es „Aufteilen“: weitere Positionen eintragen, die erste rechnet sich als Rest, „+“ bzw. „Pfand-Rückgabe“ für Einnahmen. Gespeichert wird erst, wenn die Summe dem Bankbetrag entspricht.
- Kategorie einer Bankbuchung später ändern (Bearbeiten oder in „Automatisch gebucht“) → die Regel für die Gegenseite wird angepasst.

## Dateien

| Datei | Inhalt |
|---|---|
| `tracker/collector/collector.py` | Sammler: `setup`, `run`, `run -i`, `status`, `forget` |
| `tracker/collector/bank.py` | Händlernamen bereinigen, Kaufdatum, Kennung (Tests: `test_bank.py`) |
| `tracker/collector/mail.py` | Gmail IMAP, PayPal-Parser (Tests: `test_mail.py`) |
| `tracker/collector/config.toml` | lokale Konfiguration mit Produkt-ID, **nicht in Git** |
| `tracker/data/bank_inbox.json` | letzter Abruf (wird bei jedem Lauf ersetzt) |
| `tracker/data/inbox/receipts/` | PDF-Belege aus Gmail, bis sie zugeordnet sind |
| `tracker/data/bank_rules.json` | gelernte Gegenseiten → Kategorie |
| `tracker/data/bank_ack.json` | Übernahme-Datum, ignorierte Umsätze, erledigte Belege |
| `%LOCALAPPDATA%\TrackMyBudget\` | Python-Umgebung, FinTS-Sitzung, `collector.log` (außerhalb von OneDrive) |

## Sicherheit

- ING-Passwort und Gmail-App-Passwort liegen nur im Windows-Anmeldeinformationsspeicher. `collector.py forget` löscht sie.
- Der Sammler liest nur: Umsätze, Salden, Depotbestand. Überweisungen sind nicht eingebaut.
- Gmail: gelesen werden nur Treffer der Suchen in `mail.py` (Werbung, soziale Netzwerke und gesendete Mails ausgenommen, letzte 40 Tage). Gelesene Mail-IDs merkt sich der Sammler in `data/inbox/mail_seen.json`; zugeordnete PDF-Belege bekommen in Gmail das Label `TMB/erledigt`. Ergebnisse: `data/inbox/paypal.json`, `orders.json` (Amazon), `mails.json` (Beträge), `receipts/` (PDFs).
