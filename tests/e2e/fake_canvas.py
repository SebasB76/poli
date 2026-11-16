"""A local Canvas that serves the synthetic fixtures under fixtures/canvas/ the way the aula does: Bearer auth,
personal-token creation and deletion (no listing: ESPOL's
Canvas answers 404 to it), `Link` pagination (2 items per page, so the client must follow it),
`X-Rate-Limit-Remaining`, one 429 to exercise the backoff, and file downloads. Any other write answers 405.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_SIZE = 2
DOWNLOADS = {5001: "capitulo-3-derivadas.pdf", 5005: "capitulo-3-derivadas.pdf", 5101: "semana-2-cinematica.pptx",
             5010: "silabo-matg1049.pdf", 5301: "silabo-matg1049.pdf", 5102: "lectura-vectores-escaneada.pdf",
             6101: "taller-2-especificacion.pdf", 6102: "plantilla-taller.doc", 5006: "taller-2-especificacion.pdf"}
THROTTLE_ONCE = "/api/v1/courses/102/assignments"
EMPTY_UNLESS_GIVEN = re.compile(r"courses/\d+/(students/submissions|users|forums)|calendar_events|conversations"
                                r"|users/self/groups|planner/items")


class FakeCanvas:
    def __init__(self, token: str):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.owner = self
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.lock = threading.Lock()
        self.token_ids = {token: 1}
        self.purposes = {token: "Token personal"}
        self.valid = {token}
        self.deleted: set[int] = set()
        self.next_id = 2
        self.throttled = False
        self.writes: list[tuple[str, str]] = []  # every request that was not a GET

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def mint(self, token: str) -> None:
        """A token the student created by hand in the aula."""
        with self.lock:
            self.token_ids[token], self.purposes[token] = self.next_id, "Token personal"
            self.next_id += 1
            self.valid.add(token)

    def expire(self, token: str) -> None:
        with self.lock:
            self.valid.discard(token)

    def token_json(self, token: str, *, fresh: bool = False) -> dict:
        """canvas-lms lib/api/v1/token.rb: the value only as visible_token, and whole only at creation."""
        return {"id": self.token_ids[token], "purpose": self.purposes[token], "app_name": "User-Generated",
                "workflow_state": "deleted" if self.token_ids[token] in self.deleted else "active",
                "visible_token": token if fresh else f"{token[:5]}..."}

    def load(self, api_path: str):
        path = FIXTURES / "canvas" / "state1" / (api_path.strip("/") + ".json")
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8").replace("{{BASE}}", self.base)
                          .replace("{{WEB}}", "http://web.invalid"))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, content_type="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data, headers=None):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), headers=headers)

    @property
    def canvas(self) -> FakeCanvas:
        return self.server.owner

    def _authorized(self) -> bool:
        return self.headers.get("Authorization", "").removeprefix("Bearer ") in self.canvas.valid

    def _write(self) -> bool:
        with self.canvas.lock:
            self.canvas.writes.append((self.command, urlsplit(self.path).path))
        if not self._authorized():
            self._json(401, {"errors": [{"message": "Invalid access token."}]})
            return False
        return True

    def do_PUT(self):
        self._write() and self._json(405, {"errors": [{"message": "solo lectura"}]})

    do_PATCH = do_PUT

    def do_POST(self):
        if not self._write():
            return
        if urlsplit(self.path).path != "/api/v1/users/self/tokens":
            return self._json(405, {"errors": [{"message": "solo lectura"}]})
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        purpose = (payload.get("token") or {}).get("purpose")
        if not purpose:
            return self._json(400, [{"message": "token[purpose] is missing"}])
        canvas = self.canvas
        with canvas.lock:
            token = f"7~{canvas.next_id:03d}-renewed-e2e-xxxxxxxxxxxxxxxx"
            canvas.token_ids[token], canvas.purposes[token] = canvas.next_id, purpose
            canvas.next_id += 1
            canvas.valid.add(token)
            return self._json(200, canvas.token_json(token, fresh=True))

    def do_DELETE(self):
        if not self._write():
            return
        if urlsplit(self.path).path == "/login/oauth2/token":  # a token revoking itself
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            with self.canvas.lock:
                self.canvas.valid.discard(token)
                self.canvas.deleted.add(self.canvas.token_ids[token])
            return self._json(200, {})
        match = re.fullmatch(r"/api/v1/users/self/tokens/(\d+)", urlsplit(self.path).path)
        if not match:
            return self._json(405, {"errors": [{"message": "solo lectura"}]})
        canvas = self.canvas
        with canvas.lock:
            found = next((t for t, i in canvas.token_ids.items() if i == int(match[1])), None)
            if not found or int(match[1]) in canvas.deleted:
                return self._json(404, {"errors": [{"message": "The specified resource does not exist."}]})
            canvas.valid.discard(found)
            canvas.deleted.add(int(match[1]))
            return self._json(200, canvas.token_json(found))

    def do_GET(self):
        canvas, url = self.canvas, urlsplit(self.path)
        if not self._authorized():
            return self._json(401, {"errors": [{"message": "Invalid access token."}]})
        quota = {"X-Rate-Limit-Remaining": "599.12"}
        if url.path == "/api/v1/users/self":
            return self._json(200, {"id": 42, "name": "Estudiante E2E"}, quota)
        if url.path.startswith("/files/") and url.path.endswith("/download"):
            name = DOWNLOADS.get(int(url.path.split("/")[2]))
            if not name:
                return self._json(404, {"errors": [{"message": "The specified resource does not exist."}]})
            return self._send(200, (FIXTURES / "files" / name).read_bytes(), "application/octet-stream", quota)
        if not url.path.startswith("/api/v1/"):
            return self._json(404, {"errors": [{"message": "The specified resource does not exist."}]})
        api = url.path[len("/api/v1/"):]
        if api.endswith("/discussion_topics") and ("only_announcements", "true") not in parse_qsl(url.query):
            api = api.removesuffix("discussion_topics") + "forums"  # the same path serves forums and announcements
        if url.path == THROTTLE_ONCE and not canvas.throttled:
            canvas.throttled = True
            return self._send(429, b"429 Too Many Requests (Rate Limit Exceeded)", "text/plain",
                              {"X-Rate-Limit-Remaining": "0.0", "Retry-After": "0"})
        data = canvas.load(api)
        if data is None and EMPTY_UNLESS_GIVEN.fullmatch(api):
            data = []
        if data is None:
            return self._json(404, {"errors": [{"message": "The specified resource does not exist."}]}, quota)
        if isinstance(data, dict) and "__status" in data:
            return self._json(data["__status"], data["__body"], quota)
        if not isinstance(data, list):
            return self._json(200, data, quota)
        query = parse_qsl(url.query, keep_blank_values=True)
        page = int(dict(query).get("page", 1))
        per_page = min(int(dict(query).get("per_page", 10)), PAGE_SIZE)

        def page_url(n):
            return f'<{canvas.base}{url.path}?{urlencode([(k, v) for k, v in query if k != "page"] + [("page", n)])}>'
        links = [f'{page_url(page)}; rel="current"']
        if page * per_page < len(data):
            links.append(f'{page_url(page + 1)}; rel="next"')
        return self._json(200, data[(page - 1) * per_page: page * per_page], {**quota, "Link": ",".join(links)})
