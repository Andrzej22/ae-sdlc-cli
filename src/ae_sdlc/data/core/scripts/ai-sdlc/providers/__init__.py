"""Provider adapters. Only the adapters selected at `ae-sdlc init` time are installed."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".ai-sdlc" / "manifest.json"

PREPARE_TAIL = (
    "Non-interactive invocation. If blocking input is missing, persist the question "
    "and return NEEDS_INPUT; do not invent an answer."
)
EXECUTE_TAIL = (
    "Thin execution mode. Do not call harness helper scripts or start "
    "quality/review/verification phases."
)
BACKGROUND_TAIL = (
    "Background workflow constraints:\n"
    "- runner_owns_status=true\n"
    "- implement only this spec\n"
    "- do not call ./ai-sdlc or scripts/ai-sdlc/*\n"
    "- do not spawn subagents or run quality/review/verification phases\n"
    "- return the structured result and stop"
)


def installed() -> list[str]:
    try:
        return list(json.loads(MANIFEST.read_text(encoding="utf-8")).get("providers", []))
    except Exception:
        return []


def resolve(name: str | None = None) -> str:
    """--provider flag > $AE_SDLC_PROVIDER > the only installed provider."""
    name = name or os.environ.get("AE_SDLC_PROVIDER")
    have = installed()
    if name:
        if have and name not in have:
            raise SystemExit(f"provider '{name}' is not installed here (installed: {', '.join(have)})")
        return name
    if len(have) == 1:
        return have[0]
    raise SystemExit(
        "several providers installed; choose one with --provider or $AE_SDLC_PROVIDER "
        f"(installed: {', '.join(have) or 'none'})"
    )


def load(name: str):
    return importlib.import_module(f"providers.{name}")
