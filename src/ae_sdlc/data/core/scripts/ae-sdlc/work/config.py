"""Backend selection. .ae-sdlc/work.toml is the only authoritative setting; absent means local."""
from __future__ import annotations

import tomllib
from pathlib import Path

from work_store import WorkStore

from .backend import WorkBackend, WorkConfigError
from .backends.local import LocalWorkBackend

DEFAULT_BACKEND = "local"


def backend_name(root: Path) -> str:
    path = root / ".ae-sdlc" / "work.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_BACKEND
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise WorkConfigError(f"cannot read {path}: {exc}") from exc
    work = data.get("work", {})
    name = work.get("backend", DEFAULT_BACKEND) if isinstance(work, dict) else None
    if not isinstance(name, str):
        raise WorkConfigError(f"{path}: [work] backend must be a string")
    return name


def load_backend(root: Path) -> WorkBackend:
    name = backend_name(root)
    if name == "local":
        return LocalWorkBackend(WorkStore(root))
    raise WorkConfigError(f"work backend {name!r} is not available; supported backends: local")
