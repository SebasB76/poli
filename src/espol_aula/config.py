"""Settings, the token file and the clock.

Everything lives in the user's folders, so `uvx espol-aula` works from anywhere:
  ESPOL_AULA_HOME  config folder: config.toml (optional) and token.env (default ~/.config/espol-aula)
  ESPOL_AULA_DATA  data folder: the SQLite copy and downloaded material, over [storage] data_dir
                   (default ~/.local/share/espol-aula)
  ESPOL_AULA_NOW   ISO-8601 timestamp with offset that replaces the current time (tests)
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class CoreConfig:
    canvas_url: str
    cache_minutes: int
    tz: ZoneInfo
    material_extensions: tuple[str, ...]
    max_file_mb: float
    max_mb_per_sync: float
    request_interval: float
    data_dir: Path
    config_path: Path
    raw: dict = field(repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "espol.db"

    @property
    def materials_dir(self) -> Path:
        return self.data_dir / "materiales"


def home() -> Path:
    return Path(os.environ.get("ESPOL_AULA_HOME") or _xdg("XDG_CONFIG_HOME", ".config") / "espol-aula")


def config_path() -> Path:
    return home() / "config.toml"


def secrets_path() -> Path:
    return home() / "token.env"


def section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] en config.toml debe ser una sección")
    return value


def load_config(path: Path | None = None) -> CoreConfig:
    path = path or config_path()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml tiene un error de formato: {exc}") from exc

    canvas = section(raw, "canvas")
    general = section(raw, "general")
    material = section(raw, "material")
    storage = section(raw, "storage")

    try:
        tz = ZoneInfo(str(general.get("timezone", "America/Guayaquil")))
    except Exception as exc:
        raise ConfigError(f"timezone inválida: {general.get('timezone')!r}") from exc

    canvas_url = str(canvas.get("url", "https://aulavirtual.espol.edu.ec")).rstrip("/")
    if not canvas_url.startswith(("https://", "http://")):
        raise ConfigError("canvas.url debe empezar con https://")

    return CoreConfig(
        canvas_url=canvas_url,
        cache_minutes=max(0, int(canvas.get("cache_minutes", 10))),
        tz=tz,
        material_extensions=tuple(str(e).lower().lstrip(".") for e in material.get("extensions", ["pdf", "pptx", "docx", "doc", "xlsx"])),
        max_file_mb=float(material.get("max_file_mb", 200)),
        max_mb_per_sync=max(0.0, float(material.get("max_mb_per_sync", 50))),
        request_interval=max(0.0, float(canvas.get("request_interval_seconds", 0.5))),
        data_dir=Path(os.path.expanduser(os.environ.get("ESPOL_AULA_DATA") or storage.get("data_dir")
                                         or _xdg("XDG_DATA_HOME", ".local/share") / "espol-aula")),
        config_path=path,
        raw=raw,
    )


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_secret_values(path: Path | None = None) -> dict[str, str]:
    path = path or secrets_path()
    try:
        return parse_env_file(path)
    except FileNotFoundError as exc:
        raise ConfigError("Todavía no tienes un token guardado: corre `espol-aula login`.") from exc


def update_secret_values(values: dict[str, str], *, remove: tuple[str, ...] = (),
                         path: Path | None = None) -> bool:
    """Atomically update selected keys in the gitignored secrets file.

    Values are never returned or logged. The replacement is written beside the target,
    fsynced, chmodded to 0600 and then renamed over it, so readers see either complete file.
    """
    path = path or secrets_path()
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = ""
    wanted = set(values)
    removed = set(remove) - wanted
    seen: set[str] = set()
    lines: list[str] = []
    for line in original.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in removed:
            continue
        if key in wanted:
            if key not in seen:
                lines.append(f"{key}={values[key]}")
                seen.add(key)
            continue
        lines.append(line)
    lines.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    updated = "\n".join(lines) + "\n"
    if updated == original:
        os.chmod(path, 0o600)
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    return True


def canvas_token(path: Path | None = None) -> str:
    token = load_secret_values(path).get("CANVAS_TOKEN", "")
    if not token:
        raise ConfigError("Todavía no tienes un token guardado: corre `espol-aula login`.")
    return token


def now_utc() -> datetime:
    override = os.environ.get("ESPOL_AULA_NOW")
    if override:
        value = datetime.fromisoformat(override)
        if value.tzinfo is None:
            raise ConfigError("ESPOL_AULA_NOW debe incluir zona horaria")
        return value.astimezone(timezone.utc)
    return datetime.now(timezone.utc)
