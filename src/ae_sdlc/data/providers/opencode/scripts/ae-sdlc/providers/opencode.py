from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import ROOT

NAME = "opencode"
LABEL = "opencode"

_SECRETS = {
    ".env": "deny",
    "**/.env": "deny",
    "**/.env.local": "deny",
    "**/.env.production": "deny",
    "**/.env.staging": "deny",
    "**/credentials.json": "deny",
    "**/service-account.json": "deny",
}

# SECURITY: deliberately permissive (allow-all bash + denylist) - adjust for your environment.
# Matches the interactive opencode.json permission rules, but in background
# mode destructive commands become hard denials and subagents/web tools are off.
RUNNER_PERMISSION = {
    "read": dict(_SECRETS),
    "edit": dict(_SECRETS),
    "bash": {
        "*": "allow",
        "git push*": "deny",
        "terraform apply*": "deny",
        "terraform destroy*": "deny",
        "terraform import*": "deny",
        "kubectl apply*": "deny",
        "kubectl delete*": "deny",
        "kubectl replace*": "deny",
        "kubectl patch*": "deny",
    },
    "webfetch": "deny",
    "websearch": "deny",
    "task": "deny",
}


def check_cli() -> None:
    if shutil.which("opencode") is None:
        raise SystemExit("opencode CLI not found on PATH")


def extract_structured(stdout: str) -> dict | None:
    """Parse the last assistant text part from `opencode run --format json`."""
    last_text = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("type") == "text" and part.get("text"):
            last_text = part["text"]
    if last_text is None:
        return None
    start = last_text.find("{")
    end = last_text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(last_text[start:end + 1])
    except Exception:
        return None


def run(skill: str, args: str, tail: str, schema: Path, model: str | None,
        background: bool = False) -> tuple[int, dict | None]:
    """One fresh headless opencode run of the `skill` command; returns (exit code, structured result)."""
    check_cli()
    prompt = f"{args}\n\n{tail}\n\nOutput schema:\n{schema.read_text(encoding='utf-8')}"
    # SECURITY: deliberately permissive defaults - adjust for your environment.
    # The project ships without execution restrictions on purpose: the agent runs as your
    # user, can run arbitrary shell commands and can read any file you can (including the
    # tracker credentials file). Unattended runs refuse to start outside a declared
    # sandbox (see "Security model" in the project README).
    # Here: --auto approves tool calls; RUNNER_PERMISSION allows any bash command except a
    # short denylist. A denylist is not a boundary - switch "*" to "ask"/"deny" and allowlist.
    cmd = ["opencode", "run", "--command", skill, prompt, "--format", "json", "--auto"]
    if model:
        cmd += ["--model", model]
    env = None
    if background:
        env = os.environ.copy()
        env["OPENCODE_PERMISSION"] = json.dumps(RUNNER_PERMISSION)
    proc = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    result = extract_structured(proc.stdout)
    if result is None and proc.returncode != 0:
        return proc.returncode, None
    return proc.returncode, result


REQUIRED = ["opencode.json"]
JSON_FILES = ["opencode.json"]
FORBIDDEN = [
    ".opencode/agent", ".opencode/agents", ".opencode/plugin",
    ".opencode/plugins", ".opencode/skill",
]


def skill_path(name: str) -> str:
    return f".opencode/command/{name}.md"


def extra_checks(R: Path, errors: list[str], warnings: list[str]) -> None:
    for name in ["prepare-work", "execute-work", "run-workflow", "work-status"]:
        p = R / skill_path(name)
        if p.exists() and "$ARGUMENTS" not in p.read_text(encoding="utf-8"):
            errors.append(f"command does not pass through $ARGUMENTS: {name}")
    oc = R / "opencode.json"
    if oc.exists():
        try:
            cfg = json.loads(oc.read_text(encoding="utf-8"))
            if "permission" not in cfg:
                errors.append("thin-runtime required: 'permission' missing from opencode.json")
            else:
                perm = cfg["permission"]
                if "**/.env" not in str(perm.get("read", {})) + str(perm.get("edit", {})):
                    warnings.append("opencode.json should deny read/edit of .env and credential files")
        except Exception:
            pass
