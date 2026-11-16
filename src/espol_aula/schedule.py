"""Run `espol renovar` every 10 minutes: a systemd user timer on Linux, a LaunchAgent on macOS.

ESPOL's tokens die an hour after they were made, so a computer asleep or off for an hour cuts the chain:
the next `espol login` starts a new one.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "espol-renovar"
LABEL = "ec.espol.renovar"
EVERY_SECONDS = 600


def _command() -> list[str]:
    # uvx runs from a cache folder that `uv cache clean` deletes: the timer needs an installed command
    exe = shutil.which("espol")
    if exe is None or "/.cache/uv/" in exe or "/uv/archive" in exe:
        raise RuntimeError("Para renovar solo, instala el comando con `uv tool install espol` "
                           "(o `pipx install espol`) y vuelve a correr `espol renovar --instalar`.")
    return [exe, "renovar"]


def _systemd_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def _launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def install() -> str:
    try:
        command = _command()
    except RuntimeError as exc:
        return f"⚠ {exc}"
    try:
        if sys.platform == "darwin":
            path = _launch_agent()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": command,
                                             "StartInterval": EVERY_SECONDS, "RunAtLoad": True}))
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            _run("launchctl", "load", "-w", str(path))
            return "✓ La renovación automática quedó instalada (LaunchAgent, cada 10 minutos)."
        if shutil.which("systemctl"):
            folder = _systemd_dir()
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{NAME}.service").write_text(
                f"[Unit]\nDescription=Renueva el token de Canvas de espol\n\n"
                f"[Service]\nType=oneshot\nExecStart={' '.join(command)}\n", encoding="utf-8")
            (folder / f"{NAME}.timer").write_text(
                f"[Unit]\nDescription=Renueva el token de Canvas de espol cada 10 minutos\n\n"
                f"[Timer]\nOnBootSec=1min\nOnUnitActiveSec={EVERY_SECONDS}s\n\n"
                f"[Install]\nWantedBy=timers.target\n", encoding="utf-8")
            _run("systemctl", "--user", "daemon-reload")
            _run("systemctl", "--user", "enable", "--now", f"{NAME}.timer")
            return "✓ La renovación automática quedó instalada (timer de systemd, cada 10 minutos)."
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else exc
        return f"⚠ No pude instalar la renovación automática: {detail}"
    return ("⚠ No sé programar tareas en este sistema. Programa `espol renovar` cada 10 minutos "
            "con el programador de tareas que tengas.")


def uninstall() -> str:
    if sys.platform == "darwin":
        path = _launch_agent()
        if not path.exists():
            return "No había renovación automática instalada."
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()
        return "Quité la renovación automática."
    timer = _systemd_dir() / f"{NAME}.timer"
    if not timer.exists():
        return "No había renovación automática instalada."
    subprocess.run(["systemctl", "--user", "disable", "--now", f"{NAME}.timer"], capture_output=True)
    timer.unlink()
    (_systemd_dir() / f"{NAME}.service").unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return "Quité la renovación automática."


def installed() -> str | None:
    if sys.platform == "darwin":
        return "LaunchAgent, cada 10 minutos" if _launch_agent().exists() else None
    return "timer de systemd, cada 10 minutos" if (_systemd_dir() / f"{NAME}.timer").exists() else None
