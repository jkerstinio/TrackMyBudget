"""Google Drive für den Sammler.

Gleicher Ordner wie der Tracker ("TrackMyBudget", erkennbar am appProperty tmb=root). Zugriff nur mit
drive.file: der Sammler sieht ausschließlich Dateien, die TrackMyBudget selbst angelegt hat.
Tracker und Sammler müssen dafür OAuth-Clients aus demselben Google-Cloud-Projekt verwenden.

- pull: Daten des Trackers (bank_ack.json usw.) aus Drive in den lokalen Datenordner holen. Das ist
  zugleich eine lokale Sicherung.
- push: bank_inbox.json und offene PDF-Belege nach Drive hochladen, zugeordnete Belege dort löschen.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER = "application/vnd.google-apps.folder"
KR_DRIVE = "TrackMyBudget-Drive"


def authorize(client_id: str, client_secret: str) -> str:
    """Einmalige Anmeldung im Browser (Loopback + PKCE). Gibt das Refresh-Token zurück."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in q and q.get("state", [""])[0] == state
            self.wfile.write(("<h2>TrackMyBudget</h2><p>" + ("Verbunden. Du kannst dieses Fenster schließen." if ok else "Anmeldung fehlgeschlagen.") + "</p>").encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.timeout = 300
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})
    print("Im Browser bei Google anmelden und den Zugriff erlauben …")
    webbrowser.open(url)
    t = threading.Thread(target=lambda: [srv.handle_request() for _ in range(5) if not got])
    t.start()
    t.join(300)
    srv.server_close()
    if got.get("state") != state or "code" not in got:
        raise RuntimeError("Anmeldung abgebrochen: " + got.get("error", "keine Antwort"))
    r = requests.post(TOKEN, data={"client_id": client_id, "client_secret": client_secret, "code": got["code"],
                                   "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect}, timeout=30)
    r.raise_for_status()
    tok = r.json().get("refresh_token")
    if not tok:
        raise RuntimeError("Google hat kein Refresh-Token geliefert")
    return tok


class Drive:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        r = requests.post(TOKEN, data={"client_id": client_id, "client_secret": client_secret,
                                       "refresh_token": refresh_token, "grant_type": "refresh_token"}, timeout=30)
        if r.status_code == 400:
            raise RuntimeError("Google-Freigabe abgelaufen oder widerrufen – drive_verbinden.cmd erneut starten")
        r.raise_for_status()
        self.s = requests.Session()
        self.s.headers["Authorization"] = "Bearer " + r.json()["access_token"]
        self.root = None
        self.files: dict[str, dict] = {}
        self.sub: dict[str, str] = {}

    def _req(self, method: str, url: str, **kw) -> requests.Response:
        r = self.s.request(method, url, timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Google Drive {r.status_code}: {r.text[:160]}")
        return r

    def list(self, q: str) -> list[dict]:
        out, pt = [], None
        while True:
            p = {"q": q + " and trashed=false", "fields": "nextPageToken,files(id,name,mimeType,version)", "pageSize": 1000}
            if pt:
                p["pageToken"] = pt
            j = self._req("GET", API + "/files", params=p).json()
            out += j["files"]
            pt = j.get("nextPageToken")
            if not pt:
                return out

    def mkdir(self, name: str, parent: str | None = None, props: dict | None = None) -> str:
        meta = {"name": name, "mimeType": FOLDER, **({"parents": [parent]} if parent else {}), **({"appProperties": props} if props else {})}
        return self._req("POST", API + "/files", params={"fields": "id"}, json=meta).json()["id"]

    def open(self) -> None:
        f = self.list("appProperties has { key='tmb' and value='root' }")
        self.root = f[0]["id"] if f else self.mkdir("TrackMyBudget", None, {"tmb": "root"})
        self.files, self.sub = {}, {}
        for x in self.list(f"'{self.root}' in parents"):
            (self.sub.__setitem__(x["name"], x["id"]) if x["mimeType"] == FOLDER else self.files.__setitem__(x["name"], x))

    def folder(self, name: str) -> str:
        if name not in self.sub:
            self.sub[name] = self.mkdir(name, self.root)
        return self.sub[name]

    def download(self, fid: str) -> bytes:
        return self._req("GET", f"{API}/files/{fid}", params={"alt": "media"}).content

    def upload(self, name: str, data: bytes, mime: str, parent: str, fid: str | None = None) -> dict:
        if fid:
            return self._req("PATCH", f"{UPLOAD}/files/{fid}", params={"uploadType": "media", "fields": "id,name,version"},
                             data=data, headers={"Content-Type": mime}).json()
        meta = json.dumps({"name": name, "parents": [parent]})
        files = {"metadata": ("metadata", meta, "application/json"), "file": (name, data, mime)}
        return self._req("POST", f"{UPLOAD}/files", params={"uploadType": "multipart", "fields": "id,name,version"}, files=files).json()

    def delete(self, fid: str) -> None:
        self._req("DELETE", f"{API}/files/{fid}")

    # ------------------------------------------------------------- Abgleich
    def pull(self, data: Path) -> list[str]:
        """Alle JSON-Dateien des Trackers aus Drive lokal ablegen (außer der Inbox, die schreibt der Sammler)."""
        got = []
        for name, f in self.files.items():
            if name.endswith(".json") and name != "bank_inbox.json":
                tmp = data / (name + ".tmp")
                tmp.write_bytes(self.download(f["id"]))
                tmp.replace(data / name)
                got.append(name)
        return got

    def push(self, data: Path) -> tuple[int, int]:
        """bank_inbox.json hochladen, PDF-Belege abgleichen. Gibt (hochgeladen, gelöscht) der Belege zurück."""
        inbox = data / "bank_inbox.json"
        if inbox.exists():
            f = self.files.get("bank_inbox.json")
            self.files["bank_inbox.json"] = self.upload("bank_inbox.json", inbox.read_bytes(), "application/json", self.root, f and f["id"])
        local = {p.name: p for p in (data / "inbox" / "receipts").glob("*.pdf")}
        rid = self.folder("receipts")
        remote = {x["name"]: x["id"] for x in self.list(f"'{rid}' in parents")}
        up = [n for n in local if n not in remote]
        gone = [n for n in remote if n not in local]
        for n in up:
            self.upload(n, local[n].read_bytes(), "application/pdf", rid)
        for n in gone:
            self.delete(remote[n])
        return len(up), len(gone)
