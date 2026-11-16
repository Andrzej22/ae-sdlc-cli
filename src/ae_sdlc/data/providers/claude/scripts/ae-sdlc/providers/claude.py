from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import ROOT

NAME = "claude"
LABEL = "Claude Code"
INVOKE = "/{skill}"


def check_cli() -> None:
    if shutil.which("claude") is None:
        raise SystemExit("claude CLI not found on PATH")


def run(skill: str, args: str, tail: str, schema: Path, model: str | None,
        background: bool = False) -> tuple[int, dict | None]:
    """One fresh, non-persisted headless Claude Code run; returns (exit code, structured result)."""
    # SECURITY: deliberately permissive defaults - adjust for your environment.
    # The project ships without execution restrictions on purpose: the agent runs as your
    # user, can run arbitrary shell commands and can read any file you can (including the
    # tracker credentials file). Unattended runs refuse to start outside a declared
    # sandbox (see "Security model" in the project README).
    # Here: --allowedTools Bash pre-approves every shell command. Narrow it to concrete
    # patterns (e.g. "Bash(pytest:*)", "Bash(git diff:*)") for your project.
    cmd = [
        "claude", "-p", "--no-session-persistence",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash",
        "--disallowedTools", "Agent", "WebFetch", "WebSearch",
        "--output-format", "json",
        "--json-schema", schema.read_text(encoding="utf-8"),
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(f"/{skill} {args}\n\n{tail}")
    proc = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    try:
        envelope = json.loads(proc.stdout)
    except Exception:
        return proc.returncode, None
    if envelope.get("is_error"):
        return proc.returncode or 1, None
    return proc.returncode, envelope.get("structured_output")


REQUIRED = ["CLAUDE.md", ".claude/settings.json"]
JSON_FILES = [".claude/settings.json"]
FORBIDDEN = [".claude/hooks", ".claude/agents", ".claude/commands"]


def skill_path(name: str) -> str:
    return f".claude/skills/{name}/SKILL.md"


def extra_checks(R: Path, errors: list[str], warnings: list[str]) -> None:
    settings = R / ".claude" / "settings.json"
    if settings.exists():
        try:
            if "hooks" in json.loads(settings.read_text(encoding="utf-8")):
                errors.append("thin-runtime forbidden: hooks configured in .claude/settings.json")
        except Exception:
            pass
