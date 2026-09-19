from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import ROOT

NAME = "agy"
LABEL = "Antigravity"


def check_cli() -> None:
    if shutil.which("agy") is None:
        raise SystemExit("agy CLI not found on PATH")


def run(skill: str, args: str, tail: str, schema: Path, model: str | None,
        background: bool = False) -> tuple[int, dict | None]:
    """One fresh headless Antigravity CLI run; returns (exit code, structured result)."""
    cmd = [
        "agy", "-p", f"/{skill} {args}\n\n{tail}",
        "--output-format", "json",
        "--json-schema", str(schema),
        "--mode", "accept-edits",
        "--dangerously-skip-permissions",
    ]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    try:
        envelope = json.loads(proc.stdout)
    except Exception:
        return proc.returncode, None
    if envelope.get("status") == "ERROR":
        return proc.returncode or 1, None
    return proc.returncode, envelope.get("structured_output")


REQUIRED = [".agents/rules/security.md", "GEMINI.md"]
JSON_FILES: list[str] = []
FORBIDDEN = [".agents/hooks.json", ".agents/agents"]


def skill_path(name: str) -> str:
    return f".agents/skills/{name}/SKILL.md"


def extra_checks(R: Path, errors: list[str], warnings: list[str]) -> None:
    pass
