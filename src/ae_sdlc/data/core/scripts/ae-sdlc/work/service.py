"""Deterministic lifecycle rules: eligibility, start/finish, and status reporting."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .backend import InvalidInput, InvalidTransition, RevisionConflict, WorkBackend, WorkBackendError, WorkItemNotFound
from .models import RunOutcome, Status, WorkComment, WorkItem
from .pending import PendingUpdates

_STATUS_ORDER = [Status.DRAFT, Status.READY, Status.IN_PROGRESS, Status.BLOCKED, Status.DONE]
EXECUTION_FAILED_PREFIX = "Execution failed: "
# Exit codes of failures that may pass: credentials and availability. Anything else will not change.
_RETRYABLE_EXIT_CODES = {7, 8}


@dataclass
class ExecutionContext:
    item: WorkItem
    parent: WorkItem | None
    dependencies: list[WorkItem] = field(default_factory=list)
    comments: list[WorkComment] = field(default_factory=list)
    related: list[WorkItem] = field(default_factory=list)


@dataclass
class FinishedExecution:
    disposition: str  # "completed", "blocked" or "failed"
    detail: str
    target: str = Status.DONE
    blocker: str | None = None


def decide_outcome(exit_code: int, result: dict, fallback_reason: str) -> FinishedExecution:
    """Only a successful COMPLETED run reaches DONE; everything else is BLOCKED, with its cause kept.

    A product blocker carries the agent's reason. A failed run also ends BLOCKED, on every backend,
    so that nothing repeats it automatically, but its reason says that the execution failed.
    """
    outcome = result.get("status")
    if exit_code == 0 and outcome == RunOutcome.COMPLETED:
        return FinishedExecution("completed", result.get("summary", ""))
    needs_decision = outcome in {RunOutcome.BLOCKED, RunOutcome.NEEDS_INPUT}
    default = "needs input" if needs_decision else fallback_reason
    reason = "; ".join(result.get("blockers") or []) or result.get("summary") or default
    if not needs_decision:
        reason = f"{EXECUTION_FAILED_PREFIX}{reason}"
    return FinishedExecution("blocked" if needs_decision else "failed", reason, Status.BLOCKED, reason)


class WorkService:
    def __init__(self, backend: WorkBackend, root: Path | None = None):
        self.backend = backend
        self.pending = PendingUpdates(root) if root else None

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
            related=self.backend.get_related(work_id),
        )

    def start_execution(self, work_id: str) -> WorkItem:
        return self.backend.transition(work_id, Status.IN_PROGRESS)

    def finish_execution(self, work_id: str, exit_code: int, result: dict, fallback_reason: str) -> FinishedExecution:
        """Apply an agent result to the tracker."""
        finished = decide_outcome(exit_code, result, fallback_reason)
        self.backend.transition(work_id, finished.target, blocker=finished.blocker)
        return finished

    def claim_execution(self, listed: WorkItem, comment_limit: int | None = None) -> ExecutionContext:
        """Re-read a spec the runner selected and start it, unless it changed since it was listed.

        This narrows the window between two runners and does not close it: a tracker status change
        is not a compare-and-swap, so the single-runner rule is what actually prevents a double run.
        """
        context = self.prepare_execution_context(listed.id, comment_limit)
        item = context.item
        if item.status != listed.status:
            raise InvalidTransition(f"{item.id} is now {item.status}, not {listed.status}")
        if listed.revision and item.revision and listed.revision != item.revision:
            raise RevisionConflict(f"{item.id} changed after it was selected")
        self._require_dependencies_done(context)
        context.item = self.start_execution(item.id)
        return context

    def _require_dependencies_done(self, context: ExecutionContext) -> None:
        done = {d.id for d in context.dependencies if d.status == Status.DONE}
        unmet = [d for d in context.item.dependencies if d not in done]
        if unmet:
            raise InvalidTransition(f"{context.item.id} is waiting for dependencies that are not DONE: {', '.join(unmet)}")

    def apply_update(self, update: dict) -> list[dict]:
        """Apply an owed update and return the comments the tracker refused for good.

        Every step is safe to repeat: a status already confirmed is left alone, and comments carry
        dedupe keys. A refused comment is reported instead of keeping the status update pending.
        """
        item = self.backend.get(update["work_id"])
        target, blocker = update["target_status"], update.get("blocker")
        if item.status != target or (blocker and item.blocker != blocker):
            if item.status != target and item.status not in update["allowed_from"]:
                raise InvalidTransition(f"{item.id} is {item.status}; expected one of {', '.join(update['allowed_from'])}")
            self.backend.transition(item.id, target, blocker=blocker)
        refused = []
        for comment in update.get("comments", []):
            try:
                self.backend.add_comment(comment["work_id"], comment["text"], comment["dedupe_key"])
            except WorkBackendError as exc:
                if exc.exit_code in _RETRYABLE_EXIT_CODES:
                    raise
                refused.append({"work_id": comment["work_id"], "reason": str(exc)})
        return refused

    def recover(self, work_id: str | None = None) -> dict:
        """Finish owed tracker updates. This never runs a coding agent."""
        recovered, remaining = [], []
        for update in self.pending.all():
            if work_id and update["work_id"] != work_id:
                continue
            try:
                refused = self.apply_update(update)
            except WorkBackendError as exc:
                remaining.append({"id": update["work_id"], "error": str(exc)})
                continue
            self.pending.remove(update["work_id"])
            recovered.append({"id": update["work_id"], "status": update["target_status"], "refused_comments": refused})
        return {"recovered": recovered, "remaining": remaining}

    def requeue(self, work_id: str) -> WorkItem:
        """Record the explicit decision to run a stopped spec again."""
        if self.pending.load(work_id):
            raise InvalidTransition(f"{work_id} has a finished run whose update is owed; run recover instead")
        item = self.backend.get(work_id)
        if item.kind != "spec" or item.status not in {Status.IN_PROGRESS, Status.BLOCKED}:
            raise InvalidTransition(f"{item.id} is {item.status}; only a stopped spec can be requeued")
        return self.backend.transition(item.id, Status.READY)

    def epic_progress(self, epic_id: str) -> dict:
        """Count an epic's specs. It is complete only when it has specs and all of them are DONE."""
        counts = Counter(str(s.status) for s in self.backend.list_specs(epic_id))
        total = sum(counts.values())
        return {"id": epic_id, "total": total, "counts": dict(counts), "complete": total > 0 and counts[Status.DONE] == total}

    def list_scope(self, scope: str, status: str | None = None, eligible: bool = False) -> list[WorkItem]:
        """Like list_specs, but a mistyped scope is an error rather than an empty result."""
        self.backend.get(scope)
        if eligible:
            return self.eligible_specs(scope, status or Status.READY)
        return self.backend.list_specs(scope, status=status)

    def begin_execution(self, work_id: str, comment_limit: int | None = None) -> tuple[ExecutionContext, bool]:
        """Validate a spec, move it to IN_PROGRESS and return (context, resumed).

        A spec already IN_PROGRESS is returned with resumed=True, so that a call whose response was
        lost can be repeated. Reading first means a failed read leaves the status untouched.
        """
        context = self.prepare_execution_context(work_id, comment_limit)
        item = context.item
        if item.kind != "spec":
            raise InvalidTransition(f"{item.id} is an {item.kind}; only a spec can be started")
        if self.pending and self.pending.load(item.id):
            raise InvalidTransition(f"{item.id} has a finished run whose update is owed; run ./ae-sdlc work recover")
        if item.status == Status.IN_PROGRESS:
            return context, True
        if item.status != Status.READY:
            detail = f": {item.blocker}" if item.blocker else ""
            raise InvalidTransition(f"{item.id} is {item.status}, not READY{detail}")
        self._require_dependencies_done(context)
        context.item = self.start_execution(item.id)
        return context, False

    def complete_execution(self, work_id: str, result: dict) -> tuple[WorkItem, FinishedExecution, bool]:
        """Apply a reported result to a started spec and return (item, finished, repeated).

        A spec already in the status this result leads to is left alone, so a retry is safe.
        """
        if not isinstance(result, dict):
            raise InvalidInput("the result must be a JSON object")
        outcome = result.get("status")
        if outcome not in set(RunOutcome):
            raise InvalidInput(f"result status must be one of {', '.join(RunOutcome)}")
        blockers = result.get("blockers", [])
        if not isinstance(blockers, list) or not all(isinstance(b, str) for b in blockers):
            raise InvalidInput("result blockers must be a list of strings")
        if not isinstance(result.get("summary", ""), str):
            raise InvalidInput("result summary must be a string")

        item = self.backend.get(work_id)
        target = Status.DONE if outcome == RunOutcome.COMPLETED else Status.BLOCKED
        if item.status == target:
            needs_decision = outcome in {RunOutcome.BLOCKED, RunOutcome.NEEDS_INPUT}
            disposition = "completed" if target == Status.DONE else "blocked" if needs_decision else "failed"
            return item, FinishedExecution(disposition, result.get("summary", "")), True
        if item.status != Status.IN_PROGRESS:
            raise InvalidTransition(f"{item.id} is {item.status}; only an IN_PROGRESS spec can be finished")
        finished = self.finish_execution(item.id, 0, result, fallback_reason=f"agent reported {outcome}")
        return self.backend.get(item.id), finished, False

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
