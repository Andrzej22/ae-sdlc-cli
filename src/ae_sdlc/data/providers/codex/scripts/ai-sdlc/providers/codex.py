from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import ROOT

NAME = "codex"
LABEL = "Codex"


def check_cli() -> None:
    if shutil.which("codex") is None:
        raise SystemExit("codex CLI not found on PATH")


def run(skill: str, args: str, tail: str, schema: Path, model: str | None,
        background: bool = False) -> tuple[int, dict | None]:
    """One fresh, ephemeral `codex exec`; returns (exit code, structured result)."""
    runtime = ROOT / ".ai-sdlc-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="result-", suffix=".json", dir=runtime)
    os.close(fd)
    out = Path(name)
    out.unlink(missing_ok=True)
    cmd = [
        "codex", "exec", "--ephemeral",
        "--config", 'default_permissions="project-edit"',
        "--output-schema", str(schema),
        "-o", name,
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(f"${skill} {args}\n\n{tail}")
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    result = None
    if out.exists():
        try:
            result = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            result = None
        out.unlink(missing_ok=True)
    return rc, result


REQUIRED = [".codex/config.toml", ".codex/rules/default.rules"]
JSON_FILES: list[str] = []
FORBIDDEN = [
    ".codex/hooks.json", ".codex/agents/verifier.toml",
    ".codex/agents/reviewer.toml", ".codex/agents/researcher.toml",
]


def skill_path(name: str) -> str:
    return f".agents/skills/{name}/SKILL.md"


def extra_checks(R: Path, errors: list[str], warnings: list[str]) -> None:
    pass
