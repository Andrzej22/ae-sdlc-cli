"""Local state that makes unattended runs recoverable: owed tracker updates and the runner lock."""
from __future__ import annotations

import fcntl
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .backend import WorkBackendError
from .config import atomic_write


class RunnerActive(WorkBackendError):
    code = "conflict"
    exit_code = 4


class PendingUpdates:
    """Write-ahead records of updates a finished run owes the tracker, one file per work item.

    A record is saved before the tracker is called and removed only after the provider confirmed the
    update, so a crash at any point leaves something `work recover` can finish without new code.
    """

    def __init__(self, root: Path):
        self.root = root
        self.directory = root / ".ae-sdlc-runtime" / "pending"

    def _path(self, work_id: str) -> Path:
        return self.directory / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', work_id)}.json"

    def save(self, update: dict) -> None:
        atomic_write(self._path(update["work_id"]), json.dumps(update, indent=2, ensure_ascii=False) + "\n", 0o644)

    def load(self, work_id: str) -> dict | None:
        try:
            return json.loads(self._path(work_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def all(self) -> list[dict]:
        if not self.directory.is_dir():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.directory.glob("*.json"))]

    def remove(self, work_id: str) -> None:
        self._path(work_id).unlink(missing_ok=True)


@contextmanager
def runner_lock(root: Path) -> Iterator[None]:
    """Hold the project's single-runner lock. The operating system releases it if the process dies."""
    path = root / ".ae-sdlc-runtime" / "runner.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunnerActive("another workflow runner or recovery is active in this project") from exc
        yield
