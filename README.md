# TrackMyBudget

Haushaltsbuch als einzelne HTML-Datei. Läuft komplett im Browser, ohne Server und ohne Konto.

- **`tracker/index.html`**: der Tracker. Die Daten liegen als JSON-Dateien in einem Ordner deiner Wahl (Edge/Chrome, Ordner-API) oder im Browser-Speicher.
- **`tracker/collector/`**: optionaler Sammler für Windows. Holt Umsätze per FinTS von der ING und Belege, PayPal- und Amazon-Mails aus Gmail und legt sie für den Tracker ab. Beschreibung: [`tracker/AUTOMATISIERUNG.md`](tracker/AUTOMATISIERUNG.md).

Funktionen: Buchungen mit Kategorien und Tags, Monats- und Jahresbudgets, wiederkehrende Buchungen, Auswertungen, Depot, Kassenbons (PDF) lesen und aufteilen, Bankumsätze abgleichen.

Persönliche Daten (`tracker/data/`, `tracker/collector/config.toml`) sind von Git ausgeschlossen. Passwörter liegen nur im Windows-Anmeldeinformationsspeicher.
