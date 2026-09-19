from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import ROOT

NAME = "cursor"
LABEL = "Cursor Agent CLI"


def agent_cmd() -> list[str]:
    for name in ("agent", "cursor-agent"):
        path = shutil.which(name)
        if path:
            return [path]
    cursor = shutil.which("cursor")
    if cursor:
        return [cursor, "agent"]
    raise SystemExit("Cursor Agent CLI not found on PATH (expected `agent`, `cursor-agent`, or `cursor agent`)")


def check_cli() -> None:
    agent_cmd()


def extract_json_object(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def extract_structured(stdout: str) -> dict | None:
    """Parse `cursor agent -p --output-format json` and extract the schema object."""
    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        envelope = json.loads(stdout)
    except Exception:
        return extract_json_object(stdout)
    if not isinstance(envelope, dict):
        return extract_json_object(stdout)
    if envelope.get("is_error"):
        return None
    if envelope.get("type") == "result" or "result" in envelope:
        text = envelope.get("result")
        if isinstance(text, dict):
            return text
        if isinstance(text, str):
            return extract_json_object(text)
        return None
    if "status" in envelope:
        return envelope
    return extract_json_object(stdout)


def run(skill: str, args: str, tail: str, schema: Path, model: str | None,
        background: bool = False) -> tuple[int, dict | None]:
    """One fresh headless Cursor Agent CLI run; returns (exit code, structured result)."""
    prompt = (
        f"/{skill} {args}\n\n{tail}\n\n"
        "Non-interactive structured output required.\n"
        "Satisfy this JSON schema exactly as the final response:\n"
        f"{schema.read_text(encoding='utf-8')}\n"
    )
    cmd = agent_cmd() + [
        "-p", "--force", "--trust",
        "--sandbox", "disabled",
        "--output-format", "json",
        "--workspace", str(ROOT),
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    proc = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    return proc.returncode, extract_structured(proc.stdout)


REQUIRED = [".cursor/cli.json", ".cursor/rules/security.mdc"]
JSON_FILES = [".cursor/cli.json"]
FORBIDDEN = [".cursor/hooks.json", ".cursor/hooks", ".cursor/agents", ".cursor/commands"]


def skill_path(name: str) -> str:
    return f".cursor/skills/{name}/SKILL.md"


def extra_checks(R: Path, errors: list[str], warnings: list[str]) -> None:
    for name in ["prepare-work", "execute-work", "run-workflow", "work-status"]:
        p = R / skill_path(name)
        if p.exists() and "disable-model-invocation: true" not in p.read_text(encoding="utf-8"):
            errors.append(f"skill must set disable-model-invocation: true: {name}")
    cli_json = R / ".cursor" / "cli.json"
    if cli_json.exists():
        try:
            cfg = json.loads(cli_json.read_text(encoding="utf-8"))
            deny = " ".join(cfg.get("permissions", {}).get("deny", []))
            if "**/.env" not in deny:
                warnings.append(".cursor/cli.json should deny read/write of .env and credential files")
            if "git push" not in deny:
                warnings.append(".cursor/cli.json should deny git push / terraform / kubectl mutations")
        except Exception:
            pass
