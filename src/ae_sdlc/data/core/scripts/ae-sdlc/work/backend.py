"""The storage contract. Backends do I/O and normalization; status policy lives in WorkService."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import WorkComment, WorkItem


class WorkBackendError(Exception):
    """Base class for every expected work-management failure."""


class WorkConfigError(WorkBackendError):
    """.ae-sdlc/work.toml is unreadable or selects an unavailable backend."""


class WorkItemNotFound(WorkBackendError):
    pass


class WorkItemExists(WorkBackendError):
    pass


class InvalidTransition(WorkBackendError):
    pass


class RevisionConflict(WorkBackendError):
    pass


class CapabilityMissing(WorkBackendError):
    pass


@runtime_checkable
class WorkBackend(Protocol):
    def get(self, work_id: str) -> WorkItem:
        """Return the item, or raise WorkItemNotFound."""

    def list_items(self) -> list[WorkItem]:
        """Return every epic and spec in the backend's scope."""

    def list_specs(self, scope: str, status: str | None = None) -> list[WorkItem]:
        """Return the specs of a spec or epic reference, sorted by id. An unknown scope is empty."""

    def create(self, item: WorkItem, idempotency_key: str | None = None) -> WorkItem:
        """Persist a new item. A retry must not produce a duplicate."""

    def update(self, item: WorkItem, expected_revision: str | None = None) -> WorkItem:
        """Replace title, description, parent and dependencies. Status changes use transition."""

    def get_dependencies(self, work_id: str) -> list[WorkItem]:
        """Return the dependencies that exist; a dangling reference is omitted."""

    def get_comments(self, work_id: str, limit: int | None = None) -> list[WorkComment]:
        ...

    def add_comment(self, work_id: str, text: str, dedupe_key: str | None = None) -> WorkComment:
        ...

    def transition(self, work_id: str, target_status: str, blocker: str | None = None) -> WorkItem:
        """Move the item to target_status. Raises InvalidTransition or WorkItemNotFound."""
