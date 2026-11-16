"""The student's path through the installed `espol` command, against a local Canvas (fake_canvas.py):

1. `login` takes a hand-made token, verifies it and starts the renewal chain.
2. `tareas`, `notas`, `archivos bajar` and `buscar` answer in JSON with English keys.
3. `renovar` leaves a fresh token alone, replaces one 45 minutes old and drops the hand-made one once the aula
   refuses it; reads keep working with the successor.
4. `mcp` answers the handshake, lists its tools and runs one over stdio.
5. When the aula refuses the current token, `renovar` and `estado` say the chain was cut, and a new `login`
   revives it; `logout` deletes the chain's tokens from the aula and the token file.
Canvas only ever sees GETs, plus the token endpoints.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fake_canvas import FakeCanvas

CLI = str(Path(sys.executable).parent / "espol")
T0 = datetime.fromisoformat("2026-09-28T06:30:00-05:00")
MANUAL = "7~manual-e2e-token-xxxxxxxxxxxxxxxx"


def at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


class Student:
    def __init__(self, tmp: Path, canvas: FakeCanvas):
        self.home = tmp / "config"
        self.home.mkdir()
        (self.home / "config.toml").write_text(f'[canvas]\nurl = "{canvas.base}"\n', encoding="utf-8")
        self.env = {**os.environ, "ESPOL_AULA_HOME": str(self.home), "ESPOL_AULA_DATA": str(tmp / "data")}

    def run(self, *args: str, minutes: int = 0, stdin: str = "", ok: bool = True) -> subprocess.CompletedProcess:
        done = subprocess.run([CLI, *args], input=stdin, capture_output=True, text=True, timeout=120,
                              env={**self.env, "ESPOL_AULA_NOW": at(minutes)})
        assert (done.returncode == 0) == ok, f"{args}: {done.returncode}\n{done.stdout}\n{done.stderr}"
        return done

    def json(self, *args: str, minutes: int = 0, ok: bool = True):
        return json.loads(self.run(*args, "--json", minutes=minutes, ok=ok).stdout)

    def token(self) -> str:
        for line in (self.home / "token.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("CANVAS_TOKEN="):
                return line.split("=", 1)[1]
        raise AssertionError("no CANVAS_TOKEN in token.env")


def test_student_path(tmp_path):
    with FakeCanvas(MANUAL) as canvas:
        s = Student(tmp_path, canvas)

        done = s.run("login", "--stdin", "--sin-programar", stdin=MANUAL + "\n")
        assert "Token verificado y guardado" in done.stdout, done.stdout
        first = s.token()
        assert first != MANUAL and first.startswith("7~"), "login starts the chain with a successor"
        assert oct((s.home / "token.env").stat().st_mode & 0o777) == "0o600"
        status = s.json("estado")
        assert status["token_saved"] and status["renewed_at"] and not status["chain_cut"]

        pending = s.json("tareas")
        assert pending, "the fixtures have pending assignments"
        assert {"course", "assignment", "due", "late", "points", "url"} <= set(pending[0])
        grades = s.json("notas")
        assert grades and {"course", "current_score", "assignments"} <= set(grades[0])

        files = s.json("archivos", "--curso", "calculo")
        assert any(f["id"] == 5001 for f in files), [f["id"] for f in files]
        downloaded = s.json("archivos", "bajar", "5001")
        assert downloaded[0]["local_path"] and Path(downloaded[0]["local_path"]).exists()
        hits = s.json("buscar", "derivadas")
        assert hits and hits[0]["file"] and hits[0]["page"] and hits[0]["text"]

        assert s.json("renovar", minutes=20) == {"renewed": False, "chain_cut": False}
        assert s.token() == first
        canvas.expire(MANUAL)  # ESPOL's hour: the hand-made token dies
        assert s.json("renovar", minutes=45)["renewed"]
        second = s.token()
        assert second != first
        s.json("renovar", minutes=95)  # past the probe's 70 minutes: the probe was refused, renewal stays on
        assert MANUAL not in (s.home / "token.env").read_text(encoding="utf-8")
        assert s.json("tareas", "--actualizar", minutes=95)

        mcp = subprocess.Popen([CLI, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env={**s.env, "ESPOL_AULA_NOW": at(96)})
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "pending_assignments", "arguments": {"course": "calculo"}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "search_material", "arguments": {"query": "derivadas"}}}]
        out, err = mcp.communicate("\n".join(json.dumps(r) for r in requests) + "\n", timeout=120)
        replies = {r["id"]: r for r in map(json.loads, out.splitlines())}
        assert replies[1]["result"]["serverInfo"]["name"] == "espol"
        names = {t["name"] for t in replies[2]["result"]["tools"]}
        assert {"pending_assignments", "grades", "download_files", "search_material", "read_file"} <= names
        call = replies[3]["result"]
        assert not call["isError"], call
        assert all("due" in a for a in json.loads(call["content"][0]["text"]))
        assert not replies[4]["result"]["isError"] and json.loads(replies[4]["result"]["content"][0]["text"])

        canvas.expire(s.token())  # the computer slept past the hour
        assert s.json("renovar", minutes=200, ok=False)["chain_cut"]
        assert s.json("estado", minutes=200)["chain_cut"]
        stale = s.run("tareas", minutes=200)
        assert "espol login" in stale.stderr, "the saved copy answers, and the student learns what to do"

        canvas.mint("7~second-manual-xxxxxxxxxxxxxxxx")
        s.run("login", "--stdin", "--sin-programar", minutes=210, stdin="7~second-manual-xxxxxxxxxxxxxxxx\n")
        assert not s.json("estado", minutes=210)["chain_cut"]
        assert s.json("logout", minutes=211)["revoked"] >= 1
        assert not (s.home / "token.env").exists()

        others = [w for w in canvas.writes if not w[1].startswith("/api/v1/users/self/tokens")]
        assert not others, f"only the token endpoints may be written: {others}"
