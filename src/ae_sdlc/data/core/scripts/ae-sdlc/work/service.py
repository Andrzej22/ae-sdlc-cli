"""Deterministic lifecycle rules: eligibility, start/finish, and status reporting."""
from __future__ import annotations

from dataclasses import dataclass, field

from .backend import WorkBackend, WorkItemNotFound
from .models import RunOutcome, Status, WorkComment, WorkItem

_STATUS_ORDER = [Status.DRAFT, Status.READY, Status.IN_PROGRESS, Status.BLOCKED, Status.DONE]


@dataclass
class ExecutionContext:
    item: WorkItem
    parent: WorkItem | None
    dependencies: list[WorkItem] = field(default_factory=list)
    comments: list[WorkComment] = field(default_factory=list)


@dataclass
class FinishedExecution:
    disposition: str  # "completed", "blocked" or "failed"
    detail: str


class WorkService:
    def __init__(self, backend: WorkBackend):
        self.backend = backend

    def _dependency_done(self, work_id: str) -> bool:
        try:
            return self.backend.get(work_id).status == Status.DONE
        except WorkItemNotFound:
            return False

    def eligible_specs(self, scope: str, status: str = Status.READY) -> list[WorkItem]:
        specs = self.backend.list_specs(scope, status=status)
        return [s for s in specs if all(self._dependency_done(d) for d in s.dependencies)]

    def prepare_execution_context(self, work_id: str, comment_limit: int | None = None) -> ExecutionContext:
        item = self.backend.get(work_id)
        return ExecutionContext(
            item=item,
            parent=self.backend.get(item.parent_id) if item.parent_id else None,
            dependencies=self.backend.get_dependencies(work_id),
            comments=self.backend.get_comments(work_id, comment_limit),
        )

    def start_execution(self, work_id: str) -> WorkItem:
        return self.backend.transition(work_id, Status.IN_PROGRESS)

    def finish_execution(self, work_id: str, exit_code: int, result: dict, fallback_reason: str) -> FinishedExecution:
        """Apply an agent result. Only a successful COMPLETED run reaches DONE."""
        outcome = result.get("status")
        if exit_code == 0 and outcome == RunOutcome.COMPLETED:
            self.backend.transition(work_id, Status.DONE)
            return FinishedExecution("completed", result.get("summary", ""))
        needs_decision = outcome in {RunOutcome.BLOCKED, RunOutcome.NEEDS_INPUT}
        default = "needs input" if needs_decision else fallback_reason
        reason = "; ".join(result.get("blockers") or []) or result.get("summary") or default
        self.backend.transition(work_id, Status.BLOCKED, blocker=reason)
        return FinishedExecution("blocked" if needs_decision else "failed", reason)

    def _items_in_scope(self, scope: str | None) -> list[WorkItem]:
        if not scope:
            return self.backend.list_items()
        try:
            direct = self.backend.get(scope)
        except WorkItemNotFound:
            return []
        return [direct, *self.backend.list_specs(scope)] if direct.kind == "epic" else [direct]

    def status_lines(self, scope: str | None = None, only_status: str | None = None) -> list[str]:
        rows = self._items_in_scope(scope)
        if only_status:
            rows = [r for r in rows if r.status == only_status]
        if not rows:
            return ["No matching durable work found."]
        lines: list[str] = []
        for status in _STATUS_ORDER:
            group = [r for r in rows if r.status == status]
            if group:
                lines.append(status.value)
            for r in group:
                extra = f" deps={r.dependencies}" if r.dependencies else ""
                extra += f" blocker={r.blocker}" if r.blocker else ""
                lines.append(f"  {r.id} [{r.kind}] {r.title}{extra}")
        return lines
