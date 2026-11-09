#!/usr/bin/env python3
"""Thin sequential background scheduler for the local backend; the logic lives in work/runner.py."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from work_store import ROOT
from work.backend import WorkBackend
from work.config import load_backend
from work.runner import run_workflow
from work.service import WorkService
import providers

SCHEMA = ROOT / ".ae-sdlc" / "schemas" / "execute-result.schema.json"
RUNTIME = ROOT / ".ae-sdlc-runtime" / "runs"


def main(argv: list[str] | None = None, backend: WorkBackend | None = None) -> int:
    @contextmanager
    def open_service() -> Iterator[WorkService]:
        yield WorkService(backend or load_backend(ROOT), root=ROOT)

    return run_workflow(ROOT, argv, providers, open_service, runs_dir=RUNTIME, schema=SCHEMA)


if __name__ == "__main__":
    raise SystemExit(main())
