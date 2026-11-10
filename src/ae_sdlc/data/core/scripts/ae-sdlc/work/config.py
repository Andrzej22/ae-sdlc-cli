"""Backend selection. .ae-sdlc/work.toml is the only authoritative setting; absent means local."""
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

from work_store import WorkStore

from .backend import WorkBackend, WorkConfigError
from .backends.local import LocalWorkBackend

DEFAULT_BACKEND = "local"


def config_path(root: Path) -> Path:
    return root / ".ae-sdlc" / "work.toml"


def read_config(root: Path) -> dict:
    path = config_path(root)
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise WorkConfigError(f"cannot read {path}: {exc}") from exc


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _value(value: object) -> str:
    """Render one value. A JSON string without ASCII escaping is a valid TOML basic string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items()) + " }"
    raise WorkConfigError(f"cannot write a TOML value of type {type(value).__name__}")


def _render_table(lines: list[str], path: list[str], table: dict) -> None:
    values = {k: v for k, v in table.items() if not isinstance(v, dict)}
    children = {k: v for k, v in table.items() if isinstance(v, dict)}
    if path and (values or not children):
        lines += [*([""] if lines else []), f"[{'.'.join(map(_key, path))}]"]
    lines += [f"{_key(k)} = {_value(v)}" for k, v in values.items()]
    for name, child in children.items():
        _render_table(lines, [*path, name], child)


def render_toml(data: dict) -> str:
    """Render parsed TOML back to text, nested tables included, so that a rewrite keeps every value."""
    lines: list[str] = []
    _render_table(lines, [], data)
    return "\n".join(lines) + "\n" if lines else ""


def atomic_write(path: Path, text: str, mode: int) -> None:
    """Replace path atomically, so a crash never leaves a partial file and mode applies from creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_config(root: Path, config: dict) -> None:
    """Rewrite work.toml from its parsed form. Comments do not survive; values and tables do."""
    atomic_write(config_path(root), render_toml(config), 0o644)


def set_backend(root: Path, name: str) -> None:
    config = read_config(root)
    config.setdefault("work", {})["backend"] = name
    write_config(root, config)


def backend_name(root: Path) -> str:
    work = read_config(root).get("work", {})
    name = work.get("backend", DEFAULT_BACKEND) if isinstance(work, dict) else None
    if not isinstance(name, str):
        raise WorkConfigError(f"{config_path(root)}: [work] backend must be a string")
    return name


def load_backend(root: Path) -> WorkBackend:
    name = backend_name(root)
    if name == "local":
        return LocalWorkBackend(WorkStore(root))
    raise WorkConfigError(f"work backend {name!r} is not available; supported backends: local")
