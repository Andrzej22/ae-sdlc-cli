"""Canonical statuses over Jira workflows, from the mapping saved in [jira] by `backend connect`.

Status categories and names cannot decide this at runtime: admins can recategorize a status, and a
status decides whether an agent runs. A status outside the mapping is reported as it is, so it is
never executed and never satisfies a dependency. DRAFT and READY may share a status, in which case
READY is an explicit label. BLOCKED without a mapped status is a label on the current status. Moves
use the transitions one issue offers now; the workflow itself is never modified.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .backend import InvalidTransition, WorkConfigError
from .models import Status
from .remote import name_of

READY_LABEL = "ae-sdlc-ready"
BLOCKED_LABEL = "ae-sdlc-blocked"
PENDING_LABEL = "ae-sdlc-pending"  # set at creation until links and status are applied

_REQUIRED = (Status.DRAFT, Status.READY, Status.IN_PROGRESS, Status.DONE)
_OWN_LABELS = {READY_LABEL, BLOCKED_LABEL, PENDING_LABEL}


def _setting(status: Status) -> str:
    return f"status_{status.value.lower()}"


class StatusPolicy:
    def __init__(self, settings: Mapping[str, str]):
        missing = [_setting(status) for status in _REQUIRED if not settings.get(_setting(status))]
        if missing:
            raise WorkConfigError(f"[jira] {', '.join(missing)} not set; run ./ae-sdlc backend connect jira")
        self.names = {status: settings[_setting(status)] for status in Status if settings.get(_setting(status))}
        lowered = [name.lower() for status, name in self.names.items() if status != Status.READY]
        ready = self.names[Status.READY].lower()
        self.shared = ready == self.names[Status.DRAFT].lower()
        if len(set(lowered)) != len(lowered) or (not self.shared and ready in lowered):
            raise WorkConfigError("[jira] only status_draft and status_ready may name the same Jira status")
        self.owners = {name.lower(): status for status, name in self.names.items()}

    @property
    def has_blocked_status(self) -> bool:
        return Status.BLOCKED in self.names

    def canonical(self, name: str, labels: Iterable[str]) -> str:
        """Return the canonical status. A Jira status outside the mapping is returned unchanged."""
        labels = set(labels)
        if PENDING_LABEL in labels:
            return Status.DRAFT
        owner = self.owners.get(name.lower())
        if owner == Status.DONE:
            return owner
        if BLOCKED_LABEL in labels:
            return Status.BLOCKED
        if owner in {Status.DRAFT, Status.READY} and self.shared:
            return Status.READY if READY_LABEL in labels else Status.DRAFT
        return owner or name

    def choose_transition(self, target: str, name: str, offered: Callable[[], list[dict]]) -> dict | None:
        """Return the transition to take; None when the issue can stay in its status."""
        current = self.owners.get(name.lower())
        if target == Status.BLOCKED and not self.has_blocked_status:
            if current != Status.DONE:
                return None  # the label records BLOCKED
            wanted = self.names[Status.READY]
        else:
            wanted = self.names[target]
        if name.lower() == wanted.lower():
            return None
        options = offered()
        chosen = next((o for o in options if name_of(o.get("to")).lower() == wanted.lower()), None)
        if chosen is None:
            names = ", ".join(name_of(o.get("to")) for o in options) or "none"
            raise InvalidTransition(f"the workflow offers no move from {name!r} to {wanted!r} ({target}); "
                                    f"available: {names}")
        return chosen

    def labels_after(self, target: str, labels: Iterable[str]) -> list[str]:
        kept = [label for label in labels if label not in _OWN_LABELS]
        if target == Status.READY and self.shared:
            kept.append(READY_LABEL)
        if target == Status.BLOCKED and not self.has_blocked_status:
            kept.append(BLOCKED_LABEL)
        return kept

    def describe(self) -> str:
        parts = [f"{status}={name}" for status, name in self.names.items()]
        if self.shared:
            parts.append(f"READY adds label {READY_LABEL}")
        if not self.has_blocked_status:
            parts.append(f"BLOCKED=label {BLOCKED_LABEL}")
        return ", ".join(parts)


def propose_mapping(statuses: list[dict]) -> dict[str, str]:
    """Suggest settings for a person to confirm: familiar names first, then the status category."""
    by_name = {name_of(s).lower(): name_of(s) for s in statuses if name_of(s)}

    def pick(names: tuple[str, ...], category: str | None) -> str | None:
        found = next((by_name[n] for n in names if n in by_name), None)
        return found or next((name_of(s) for s in statuses if category and s.get("category") == category), None)

    proposal = {"status_draft": pick(("backlog", "draft"), None) or pick(("to do", "open"), "new"),
                "status_ready": pick(("to do", "ready", "selected for development"), "new"),
                "status_in_progress": pick(("in progress",), "indeterminate"),
                "status_done": pick(("done",), "done"),
                "status_blocked": pick(("blocked",), None)}
    return {key: value for key, value in proposal.items() if value}


def check_mapping(policy: StatusPolicy, data: Any, issue_types: Iterable[str]) -> None:
    """Confirm each mapped status exists for each issue type, from listJiraStatuses mode=project."""
    payload = data.get("data", data) if isinstance(data, dict) else {}
    statuses = [s for s in payload.get("statuses") or [] if isinstance(s, dict)]
    for issue_type in issue_types:
        work = next((w for w in payload.get("workTypes") or [] if name_of(w).lower() == issue_type.lower()), None)
        ids = set(work.get("statusIds") or []) if work else {s.get("id") for s in statuses}
        available = {name_of(s).lower() for s in statuses if s.get("id") in ids}
        for status, name in policy.names.items():
            if name.lower() not in available:
                raise WorkConfigError(f"[jira] {_setting(status)}: issue type {issue_type} has no status {name!r}")
