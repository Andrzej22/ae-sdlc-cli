"""Importable view of the generated runtime's ``work`` package.

The implementation must also run from a project with no installed package, so it ships once,
under ``data/core/scripts/ae-sdlc/work``. Pointing ``__path__`` there exposes those same files
as ``ae_sdlc.work.*`` without a second copy.
"""
from __future__ import annotations

import sys

from ..installer import DATA

_SCRIPTS = DATA / "core" / "scripts" / "ae-sdlc"
__path__ = [str(_SCRIPTS / "work")]

if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))  # for the shared top-level work_store module
