"""Canonical domain objects shared by the service and every backend."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"  # optional: only on a backend whose workflow has a review step
    DONE = "DONE"
    BLOCKED = "BLOCKED"


# A spec in review is implemented and its code is in the checkout, so it unblocks its dependents.
# Only DONE completes an epic.
SATISFIES_DEPENDENCY = frozenset({Status.REVIEW, Status.DONE})


class RunOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    NEEDS_INPUT = "NEEDS_INPUT"


@dataclass
class WorkItem:
    id: str
    kind: str  # "epic" or "spec"
    title: str | None
    description: str
    # A plain string on purpose: a hand-edited file may hold a value outside Status, and reading
    # it must not fail. Status is enforced on writes, in WorkBackend.transition.
    status: str
    parent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    related_ids: list[str] = field(default_factory=list)
    blocker: str | None = None
    revision: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkComment:
    id: str
    work_item_id: str
    author: str
    body: str
    created_at: str
    updated_at: str | None = None
