"""The storage contract. Backends do I/O and normalization; status policy lives in WorkService."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import WorkComment, WorkItem


class WorkBackendError(Exception):
    """Base class for every expected work-management failure.

    `code` and `exit_code` are the stable contract of the `./ae-sdlc work` commands.
    """

    code = "backend_error"
    exit_code = 1


class WorkConfigError(WorkBackendError):
    """.ae-sdlc/work.toml is unreadable or selects an unavailable backend."""

    code = "configuration"
    exit_code = 6


class InvalidInput(WorkBackendError):
    """A command received a payload it cannot use. Nothing was changed."""

    code = "invalid_input"
    exit_code = 2


class WorkItemNotFound(WorkBackendError):
    code = "not_found"
    exit_code = 3


class WorkItemExists(WorkBackendError):
    code = "conflict"
    exit_code = 4


class InvalidTransition(WorkBackendError):
    code = "invalid_transition"
    exit_code = 5


class RevisionConflict(WorkBackendError):
    code = "conflict"
    exit_code = 4


class CapabilityMissing(WorkBackendError):
    code = "capability_missing"
    exit_code = 6


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

    def search(self, query: str, limit: int = 20) -> list[WorkItem]:
        """Find existing work by text. Dependencies need not be loaded."""

    def get_dependencies(self, work_id: str) -> list[WorkItem]:
        """Return the dependencies that exist; a dangling reference is omitted."""

    def get_related(self, work_id: str) -> list[WorkItem]:
        """Return the linked items that exist."""

    def get_comments(self, work_id: str, limit: int | None = None) -> list[WorkComment]:
        ...

    def add_comment(self, work_id: str, text: str, dedupe_key: str | None = None) -> WorkComment:
        ...

    def transition(self, work_id: str, target_status: str, blocker: str | None = None) -> WorkItem:
        """Move the item to target_status. Raises InvalidTransition or WorkItemNotFound."""
