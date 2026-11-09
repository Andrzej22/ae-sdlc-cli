"""Reach the installed package, which alone holds MCP code and reads credentials."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .backend import WorkConfigError


def _package_command(entrypoint: Path) -> list[str] | None:
    if configured := os.environ.get("AE_SDLC_BIN"):
        return shlex.split(configured)
    found = shutil.which("ae-sdlc")
    # PATH may include the project directory, where this name is the entrypoint itself.
    if found and Path(found).resolve() != entrypoint.resolve():
        return [found]
    return None


def run_package_command(root: Path, entrypoint: Path, argv: list[str]) -> int:
    """Run `ae-sdlc _work`, which inherits stdin and stdout so its JSON reaches the caller untouched."""
    command = _package_command(entrypoint)
    if command is None:
        raise WorkConfigError("linear and jira backends need the installed ae-sdlc package: "
                              "install it with pipx or uv tool, or set AE_SDLC_BIN")
    try:
        return subprocess.run([*command, "_work", "--root", str(root), *argv]).returncode
    except OSError as exc:
        raise WorkConfigError(f"cannot run the installed ae-sdlc package: {exc}") from exc


def run_backend_command(root: Path, entrypoint: Path, action: str, provider: str) -> int:
    return run_package_command(root, entrypoint, ["backend", action, provider])
