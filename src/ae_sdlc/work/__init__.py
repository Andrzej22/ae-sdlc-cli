"""Importable view of the generated runtime's ``work`` package, plus the remote-only code.

The stdlib implementation must also run from a project with no installed package, so it ships once,
under ``data/core/scripts/ae-sdlc/work``. Pointing ``__path__`` there exposes those same files as
``ae_sdlc.work.*`` without a second copy. This directory stays on the path for ``ae_sdlc.work.mcp``,
which needs the MCP SDK and therefore is never installed into a project.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..installer import DATA

_SCRIPTS = DATA / "core" / "scripts" / "ae-sdlc"
__path__ = [str(_SCRIPTS / "work"), str(Path(__file__).parent)]

if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))  # for the shared top-level work_store module
