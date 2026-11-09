"""Publish generated epics and specs through the active backend, and report the confirmed ids.

A document holds an optional epic, specs, and blocking questions. It is checked in full before the
first write. Publishing again after a partial failure is safe: backends return work they already
hold, marked as reused, instead of creating a duplicate.
"""
from __future__ import annotations

from typing import Any

from .backend import InvalidInput, InvalidTransition, WorkBackend, WorkBackendError
from .models import Status, WorkItem

_EPIC_KEYS = {"id", "title", "description", "status", "expected_revision"}
_SPEC_KEYS = _EPIC_KEYS | {"ref", "depends_on"}
_PUBLISHABLE = {Status.DRAFT, Status.READY}
_REOPENABLE = {Status.DRAFT, Status.READY, Status.BLOCKED}


def _text(entry: dict, key: str, where: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput(f"{where}: {key} must be a non-empty string")
    return value


def _checked(entry: Any, keys: set[str], where: str) -> dict:
    if not isinstance(entry, dict):
        raise InvalidInput(f"{where} must be an object")
    if unknown := sorted(set(entry) - keys):
        raise InvalidInput(f"{where}: unknown field {unknown[0]!r}")
    for key in keys - {"depends_on"}:
        _text(entry, key, where)
    if entry.get("status") is not None and entry["status"] not in _PUBLISHABLE:
        raise InvalidInput(f"{where}: status must be DRAFT or READY")
    if entry.get("id") is None and (entry.get("title") is None or entry.get("description") is None):
        raise InvalidInput(f"{where}: a new item needs a title and a description")
    return entry


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise InvalidInput(f"{where} must be a list of non-empty strings")
    return value


def _validated(doc: Any) -> tuple[dict | None, list[dict], list[str]]:
    if not isinstance(doc, dict):
        raise InvalidInput("the document must be a JSON object")
    if unknown := sorted(set(doc) - {"epic", "specs", "blocking_questions"}):
        raise InvalidInput(f"unknown field {unknown[0]!r}")
    epic = _checked(doc["epic"], _EPIC_KEYS, "epic") if doc.get("epic") is not None else None
    raw_specs = doc.get("specs", [])
    if not isinstance(raw_specs, list):
        raise InvalidInput("specs must be a list")
    specs = [_checked(s, _SPEC_KEYS, f"specs[{i}]") for i, s in enumerate(raw_specs)]
    if epic is None and not specs:
        raise InvalidInput("the document needs an epic or at least one spec")
    refs = [s["ref"] for s in specs if s.get("ref") is not None]
    if len(refs) != len(set(refs)):
        raise InvalidInput("spec refs must be unique")
    for i, spec in enumerate(specs):
        if "depends_on" in spec:
            _strings(spec["depends_on"], f"specs[{i}].depends_on")
    return epic, specs, _strings(doc.get("blocking_questions", []), "blocking_questions")


def _external_dependencies(specs: list[dict]) -> list[str]:
    """Return references to existing items. A ref must name an earlier spec of the same document."""
    all_refs = {s["ref"] for s in specs if s.get("ref") is not None}
    earlier: set[str] = set()
    external: list[str] = []
    for spec in specs:
        for dependency in spec.get("depends_on", []):
            if dependency in earlier:
                continue
            if dependency in all_refs:
                raise InvalidInput(f"{dependency!r} must be listed before the spec that depends on it")
            external.append(dependency)
        if spec.get("ref") is not None:
            earlier.add(spec["ref"])
    return external


def _is_attachment(entry: dict) -> bool:
    return not any(entry.get(k) is not None for k in ("title", "description", "status"))


def _target_status(requested: str | None, current: str | None, has_questions: bool) -> str | None:
    """Work with an open blocking question never ends up READY."""
    if has_questions and (requested or current) == Status.READY:
        return Status.DRAFT
    return requested


def _load_existing(backend: WorkBackend, entry: dict, kind: str, has_questions: bool) -> WorkItem:
    current = backend.get(entry["id"])
    if current.kind != kind:
        raise InvalidInput(f"{current.id} is an {current.kind}, not a {kind}")
    target = _target_status(entry.get("status"), current.status, has_questions)
    if not _is_attachment(entry) and target and target != current.status and current.status not in _REOPENABLE:
        raise InvalidTransition(f"{current.id} is {current.status}; publishing cannot move it to {target}")
    return current


def _report(item: WorkItem, ref: str | None = None) -> dict:
    report = {"id": item.id, "kind": item.kind, "title": item.title, "status": item.status, "url": item.url,
              "reused": bool(item.metadata.get("reused"))}
    if ref is not None:
        report["ref"] = ref
    if path := item.metadata.get("path"):
        report["path"] = path
    return report


def _apply(backend: WorkBackend, entry: dict, kind: str, current: WorkItem | None, has_questions: bool,
           parent_id: str | None, dependencies: list[str] | None) -> WorkItem:
    if current is None:
        status = _target_status(entry.get("status") or Status.DRAFT, None, has_questions)
        return backend.create(WorkItem(id="", kind=kind, title=entry["title"], description=entry["description"],
                                       status=status, parent_id=parent_id, dependencies=dependencies or []))
    if _is_attachment(entry) and dependencies is None and parent_id in (None, current.parent_id):
        return current
    target = _target_status(entry.get("status"), current.status, has_questions)
    merged = WorkItem(id=current.id, kind=kind, title=entry.get("title") or current.title,
                      description=entry.get("description") or current.description, status=current.status,
                      parent_id=parent_id or current.parent_id,
                      dependencies=current.dependencies if dependencies is None else dependencies)
    updated = backend.update(merged, entry.get("expected_revision"))
    return backend.transition(current.id, target) if target and target != current.status else updated


def publish(backend: WorkBackend, doc: Any) -> dict:
    epic, specs, questions = _validated(doc)
    has_questions = bool(questions)
    for dependency in _external_dependencies(specs):
        backend.get(dependency)
    current_epic = _load_existing(backend, epic, "epic", has_questions) if epic and epic.get("id") else None
    current_specs = [_load_existing(backend, s, "spec", has_questions) if s.get("id") else None for s in specs]

    report: dict[str, Any] = {"epic": None, "specs": [], "blocking_questions": questions}
    published_refs: dict[str, str] = {}
    try:
        parent_id = None
        if epic:
            published_epic = _apply(backend, epic, "epic", current_epic, has_questions, None, None)
            report["epic"] = _report(published_epic)
            parent_id = published_epic.id
        for spec, current in zip(specs, current_specs):
            dependencies = None
            if "depends_on" in spec:
                dependencies = [published_refs.get(d, d) for d in spec["depends_on"]]
            item = _apply(backend, spec, "spec", current, has_questions, parent_id, dependencies)
            report["specs"].append(_report(item, spec.get("ref")))
            if spec.get("ref") is not None:
                published_refs[spec["ref"]] = item.id
    except WorkBackendError as exc:
        exc.partial = report  # what already exists, so the caller can report it and retry
        raise
    return report
