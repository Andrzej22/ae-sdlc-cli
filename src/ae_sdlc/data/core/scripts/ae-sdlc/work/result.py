"""The execute-work result contract: strict parsing and a check of the agent's claims against the working tree.

Only some agent CLIs enforce `execute-result.schema.json` themselves; Cursor and opencode only see it in the
prompt. The runner therefore validates every result here, the same way for every provider, and fails closed:
an invalid result becomes FAILED, never DONE. Validation is hand-written because the installed runtime is
standard-library only; a test keeps it in step with the schema file.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .models import RunOutcome

REQUIRED = ("status", "work_items", "changed_files", "command_run", "blockers", "summary", "comments")
_STRING_LISTS = ("work_items", "changed_files", "blockers")
_COMMENT_KEYS = frozenset({"work_item_id", "text"})
_MAX_LISTED = 20


def failed_result(reason: str, summary: str = "") -> dict:
    return {"status": RunOutcome.FAILED.value, "work_items": [], "changed_files": [], "command_run": None,
            "blockers": [reason], "summary": summary, "comments": []}


def _problems(raw: object) -> list[str]:
    if not isinstance(raw, dict):
        return ["the result is not a JSON object"]
    problems = [f"missing field: {k}" for k in REQUIRED if k not in raw]
    problems += [f"unexpected field: {k}" for k in sorted(set(raw) - set(REQUIRED))]
    if "status" in raw and raw["status"] not in set(RunOutcome):
        problems.append(f"status must be one of {', '.join(RunOutcome)}")
    for key in _STRING_LISTS:
        if key in raw and not (isinstance(raw[key], list) and all(isinstance(v, str) for v in raw[key])):
            problems.append(f"{key} must be a list of strings")
    if "command_run" in raw and not (raw["command_run"] is None or isinstance(raw["command_run"], str)):
        problems.append("command_run must be a string or null")
    if "summary" in raw and not isinstance(raw["summary"], str):
        problems.append("summary must be a string")
    comments = raw.get("comments", [])
    if not isinstance(comments, list) or not all(
            isinstance(c, dict) and set(c) == _COMMENT_KEYS and all(isinstance(v, str) for v in c.values())
            for c in comments):
        problems.append("comments must be a list of {work_item_id, text} string objects")
    return problems


class InvalidResult(ValueError):
    """The agent's result does not satisfy the execute-result schema."""


def parse_execute_result(raw: object) -> dict:
    """Return the result if it satisfies the schema; otherwise raise InvalidResult naming every problem."""
    problems = _problems(raw)
    if problems:
        raise InvalidResult("invalid structured result: " + "; ".join(problems))
    return dict(raw)  # type: ignore[arg-type]  # validated above


def _normalize(path: str, root: Path) -> str:
    """Turn `./src/a.py`, `src\\a.py` or `<root>/src/a.py` into the root-relative `src/a.py`."""
    text = path.strip().replace("\\", "/")
    p = PurePosixPath(text)
    if p.is_absolute():
        try:
            p = PurePosixPath(Path(text).resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            return text  # outside the project: cannot have changed inside it, so it stays unverified
    return "/".join(part for part in p.parts if part != ".").rstrip("/")


@dataclass
class Verification:
    """How the agent's `changed_files` compare with what actually changed while it ran."""
    checked: bool
    actual: set[str] = field(default_factory=set)
    unverified_claims: list[str] = field(default_factory=list)  # claimed, but did not change
    ignored_claims: list[str] = field(default_factory=list)     # claimed, but git ignores them: not checkable
    unreported: list[str] = field(default_factory=list)         # changed, but not claimed (reported only)

    def report(self) -> dict:
        if not self.checked:
            return {"checked": False, "reason": "not a git working tree; changed_files were not verified"}
        out: dict = {"checked": True, "actual_changes": len(self.actual)}
        for name in ("unverified_claims", "ignored_claims", "unreported"):
            values = getattr(self, name)
            if values:
                out[name] = values[:_MAX_LISTED] + ([f"... {len(values) - _MAX_LISTED} more"]
                                                    if len(values) > _MAX_LISTED else [])
        return out


def verify_changes(result: dict, actual: set[str] | None, is_ignored: Callable[[str], bool],
                   root: Path) -> tuple[dict, Verification]:
    """Check a parsed result against the paths that changed during the run.

    `actual` is None when the working tree could not be inspected: nothing is verified or changed then.
    A COMPLETED result is downgraded to FAILED when the tree did not change at all, or when it claims a
    file that did not change. Changes the agent did not list (lock files, generated code) are only reported.
    """
    if actual is None:
        return result, Verification(checked=False)
    claimed = {_normalize(p, root) for p in result["changed_files"]} - {""}
    ignored = sorted(p for p in claimed if is_ignored(p))
    def covers(claim: str, path: str) -> bool:  # a claimed directory covers what changed inside it
        return path == claim or path.startswith(claim + "/")
    unverified = sorted(c for c in claimed - set(ignored) if not any(covers(c, a) for a in actual))
    unreported = sorted(a for a in actual if not any(covers(c, a) for c in claimed))
    verification = Verification(True, actual, unverified, ignored, unreported)
    if result["status"] != RunOutcome.COMPLETED:
        return result, verification
    if not actual:
        reason = "reported COMPLETED, but nothing in the working tree changed during the run"
    elif unverified:
        reason = "reported changes to files that did not change: " + ", ".join(unverified[:_MAX_LISTED])
    else:
        return result, verification
    downgraded = {**result, "status": RunOutcome.FAILED.value, "blockers": [*result["blockers"], reason]}
    return downgraded, verification
