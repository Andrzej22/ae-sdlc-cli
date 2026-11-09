"""Backend selection. .ae-sdlc/work.toml is the only authoritative setting; absent means local."""
from __future__ import annotations

import json
import os
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


def render_toml(tables: dict[str, dict[str, str]]) -> str:
    """Render tables of strings. A JSON string without ASCII escaping is a valid TOML basic string."""
    blocks = []
    for table, values in tables.items():
        lines = [f"[{table}]", *(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in values.items())]
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


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
    """Rewrite work.toml from its parsed form. Comments do not survive; values do."""
    for table, values in config.items():
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise WorkConfigError(f"{config_path(root)}: [{table}] must contain only string settings")
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
