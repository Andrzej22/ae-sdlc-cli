"""Small conventions shared by the remote backends."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .models import WorkItem

COMMENT_MARKER = re.compile(r"\s*<!--\s*ae-sdlc-[^>]*-->\s*")
BLOCKER_MARKER = "<!-- ae-sdlc-blocker -->"
BLOCKER_PREFIX = "**Blocked:** "


def op_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def name_of(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("displayName")
    return str(value) if value else ""


def sort_key(item: WorkItem) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", item.id)]
