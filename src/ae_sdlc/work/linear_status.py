"""Mapping between canonical AE-SDLC statuses and one Linear team's discovered workflow states.

Linear groups states into fixed types, so the defaults follow the type and survive renamed states.
`status_<canonical>` settings in [linear] pin a state by name. The team's workflow is never modified.

REVIEW exists only when `status_review` names a started state other than the one IN_PROGRESS uses
(for example "In Review"). Without it the team has no review step: finished work goes straight to DONE.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .backend import CapabilityMissing, WorkConfigError
from .models import Status

BLOCKED_LABEL = "ae-sdlc-blocked"
CANCELED = "CANCELED"  # outside Status on purpose: never eligible, and never satisfies a dependency

_WRITE_TYPE = {Status.DRAFT: "backlog", Status.READY: "unstarted", Status.IN_PROGRESS: "started", Status.DONE: "completed"}
_READ_TYPE = {"triage": Status.DRAFT, "backlog": Status.DRAFT, "unstarted": Status.READY,
              "started": Status.IN_PROGRESS, "completed": Status.DONE, "canceled": CANCELED}


def _setting(status: Status) -> str:
    return f"status_{status.value.lower()}"


@dataclass(frozen=True)
class StatusMapping:
    states: dict[str, str]  # canonical status -> state name; REVIEW and BLOCKED only when the team has them
    pinned: dict[str, str]  # lowercase state name -> canonical status, from settings or a native Blocked state
    types: dict[str, str]  # lowercase state name -> state type

    @property
    def has_review(self) -> bool:
        return Status.REVIEW in self.states

    @property
    def uses_blocked_label(self) -> bool:
        return Status.BLOCKED not in self.states

    def state_for(self, status: str) -> str:
        return self.states[Status.READY if status == Status.BLOCKED and self.uses_blocked_label else status]

    def canonical(self, name: str, state_type: str | None, labels: Iterable[str]) -> str:
        """Return the canonical status. A state name this team does not define is returned unchanged."""
        kind = state_type or self.types.get(name.lower())
        if kind in {"completed", "canceled"}:
            return _READ_TYPE[kind]
        if BLOCKED_LABEL in labels:
            return Status.BLOCKED
        return self.pinned.get(name.lower()) or _READ_TYPE.get(kind or "") or name

    def describe(self) -> str:
        parts = [f"{status}={state}" for status, state in self.states.items()]
        if self.uses_blocked_label:
            parts.append(f"BLOCKED={self.states[Status.READY]} + label {BLOCKED_LABEL}")
        return ", ".join(parts)


def build_status_mapping(team_states: list[dict], settings: Mapping[str, str]) -> StatusMapping:
    ordered = sorted((s for s in team_states if s.get("name")), key=lambda s: s.get("position") or 0)
    by_name = {str(s["name"]).lower(): s for s in ordered}
    types = {name: str(s.get("type") or "") for name, s in by_name.items()}

    def pinned_state(status: Status) -> str | None:
        wanted = settings.get(_setting(status))
        if wanted and wanted.lower() not in by_name:
            raise WorkConfigError(f"[linear] {_setting(status)}: the team has no state named {wanted!r}")
        return str(by_name[wanted.lower()]["name"]) if wanted else None

    states: dict[str, str] = {}
    for status, state_type in _WRITE_TYPE.items():
        name = pinned_state(status) or next((str(s["name"]) for s in ordered if s.get("type") == state_type), None)
        if name is None:
            raise CapabilityMissing(f"the team has no {state_type} state for {status}; "
                                    f"set {_setting(status)} under [linear] in .ae-sdlc/work.toml")
        states[status] = name
    if states[Status.DRAFT].lower() == states[Status.READY].lower():
        raise CapabilityMissing(f"DRAFT and READY both map to {states[Status.DRAFT]!r}; "
                                "set status_draft or status_ready to distinct states")

    review = pinned_state(Status.REVIEW)
    if review:
        # In Progress and In Review share the type "started", so a review step is only ever known by name.
        if types[review.lower()] != "started" or review.lower() == states[Status.IN_PROGRESS].lower():
            raise WorkConfigError(f"[linear] {_setting(Status.REVIEW)}: {review!r} must be a started state "
                                  f"other than {states[Status.IN_PROGRESS]!r}")
        states[Status.REVIEW] = review

    blocked = pinned_state(Status.BLOCKED)
    if blocked is None and "blocked" in by_name and types["blocked"] not in {"completed", "canceled"}:
        blocked = str(by_name["blocked"]["name"])
    if blocked:
        states[Status.BLOCKED] = blocked

    pinned = {name.lower(): status for status, name in states.items() if settings.get(_setting(Status(status)))}
    if blocked:
        pinned[blocked.lower()] = Status.BLOCKED
    return StatusMapping(states, pinned, types)
