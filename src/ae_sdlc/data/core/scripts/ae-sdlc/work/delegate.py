"""Reach the installed package, which alone holds MCP code and reads credentials."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path


def _package_command(entrypoint: Path) -> list[str] | None:
    if configured := os.environ.get("AE_SDLC_BIN"):
        return shlex.split(configured)
    found = shutil.which("ae-sdlc")
    # PATH may include the project directory, where this name is the entrypoint itself.
    if found and Path(found).resolve() != entrypoint.resolve():
        return [found]
    return None


def run_backend_command(root: Path, entrypoint: Path, action: str, provider: str) -> int:
    command = _package_command(entrypoint)
    if command is None:
        raise SystemExit("linear and jira backends need the installed ae-sdlc package: "
                         "install it with pipx or uv tool, or set AE_SDLC_BIN")
    return subprocess.run([*command, "_work", "--root", str(root), "backend", action, provider]).returncode
