"""WorkBackend over the Markdown artifact store. All parsing and rendering stays in work_store."""
from __future__ import annotations

import re
from pathlib import Path

from work_store import Record, WorkStore, render_frontmatter

from ..backend import CapabilityMissing, InvalidTransition, RevisionConflict, WorkItemExists, WorkItemNotFound
from ..models import Status, WorkComment, WorkItem


def _revision(path: Path) -> str:
    return str(path.stat().st_mtime_ns)


def _to_item(rec: Record) -> WorkItem:
    deps = rec.meta.get("dependencies") or []
    return WorkItem(
        id=rec.id,
        kind=rec.type,
        title=rec.meta.get("title"),
        description=rec.body,
        status=rec.status,
        parent_id=rec.meta.get("epic") or None,
        dependencies=[str(d) for d in deps] if isinstance(deps, list) else [],
        blocker=rec.meta.get("blocker") or None,
        revision=_revision(rec.path),
        metadata={"path": str(rec.path)},
    )


def _slug(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


class LocalWorkBackend:
    supports_review = False

    def __init__(self, store: WorkStore):
        self.store = store

    def _record(self, work_id: str) -> Record:
        rec = self.store.get(work_id) or self._record_at(work_id)
        if rec is None:
            raise WorkItemNotFound(f"unknown work item: {work_id}")
        return rec

    def _record_at(self, reference: str) -> Record | None:
        """Interactive commands also accept a spec's path."""
        target = (self.store.root / reference).resolve()
        return next((r for r in self.store.records if r.path.resolve() == target), None)

    def get(self, work_id: str) -> WorkItem:
        return _to_item(self._record(work_id))

    def list_items(self) -> list[WorkItem]:
        return [_to_item(r) for r in self.store.records]

    def list_specs(self, scope: str, status: str | None = None) -> list[WorkItem]:
        return [_to_item(r) for r in self.store.specs_for_scope(scope, status=status)]

    def search(self, query: str, limit: int = 20) -> list[WorkItem]:
        needle = query.lower()
        found = [r for r in self.store.records
                 if needle in r.id.lower() or needle in str(r.meta.get("title") or "").lower() or needle in r.body.lower()]
        return [_to_item(r) for r in found[:limit]]

    def _next_id(self, kind: str) -> str:
        prefix = "EPIC" if kind == "epic" else "SPEC"
        used = [int(m[1]) for r in self.store.records if (m := re.fullmatch(rf"{prefix}-(\d+)", r.id))]
        return f"{prefix}-{max(used, default=0) + 1:03d}"

    def _same_work(self, item: WorkItem) -> WorkItem | None:
        for known in self.list_items():
            if (known.kind, known.parent_id, (known.title or "").strip().lower()) == (
                    item.kind, item.parent_id, (item.title or "").strip().lower()):
                return known
        return None

    def _new_path(self, item: WorkItem) -> Path:
        name = "-".join(p for p in (item.id, _slug(item.title)) if p)
        product = self.store.root / "docs" / "product"
        if item.kind == "epic":
            return product / "epics" / name / "epic.md"
        if item.parent_id:
            return self._record(item.parent_id).path.parent / "specs" / f"{name}.md"
        return product / "standalone" / f"{name}.md"

    def create(self, item: WorkItem, idempotency_key: str | None = None) -> WorkItem:
        """Create the item. An explicit id is a stable key, so a retry is rejected instead of duplicated.

        Without an id one is allocated, and as on the remote backends the same title under the same
        parent counts as the same work: it is returned with metadata["reused"] set.
        """
        if item.id and self.store.get(item.id):
            raise WorkItemExists(f"work item already exists: {item.id}")
        if item.kind not in {"epic", "spec"}:
            raise ValueError(f"invalid kind: {item.kind}")
        if item.status not in set(Status) or item.status == Status.REVIEW:
            raise InvalidTransition(f"invalid status: {item.status}")
        if not item.id:
            if existing := self._same_work(item):
                existing.metadata["reused"] = True
                return existing
            item.id = self._next_id(item.kind)
        path = self._new_path(item)
        meta = {
            "artifact_type": item.kind,
            "work_item_id": item.id,
            "title": item.title,
            "status": item.status,
            "source_of_truth": "repository",
        }
        if item.kind == "spec":
            meta |= {"epic": item.parent_id, "dependencies": item.dependencies}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_frontmatter(meta, item.description), encoding="utf-8")
        self.store.scan_once()
        return self.get(item.id)

    def update(self, item: WorkItem, expected_revision: str | None = None) -> WorkItem:
        rec = self._record(item.id)
        if expected_revision is not None and _revision(rec.path) != expected_revision:
            raise RevisionConflict(f"{item.id} changed since revision {expected_revision}")
        rec.meta["title"] = item.title
        if rec.type == "spec":
            rec.meta["epic"] = item.parent_id
            rec.meta["dependencies"] = item.dependencies
        rec.body = item.description
        rec.path.write_text(render_frontmatter(rec.meta, rec.body), encoding="utf-8")
        return _to_item(rec)

    def get_dependencies(self, work_id: str) -> list[WorkItem]:
        known = (self.store.get(d) for d in self.get(work_id).dependencies)
        return [_to_item(r) for r in known if r]

    def get_related(self, work_id: str) -> list[WorkItem]:
        self._record(work_id)
        return []  # the Markdown format has no relation other than dependencies

    def get_comments(self, work_id: str, limit: int | None = None) -> list[WorkComment]:
        self._record(work_id)
        return []  # the Markdown format has no comment storage

    def add_comment(self, work_id: str, text: str, dedupe_key: str | None = None) -> WorkComment:
        raise CapabilityMissing("the local backend does not store comments")

    def transition(self, work_id: str, target_status: str, blocker: str | None = None) -> WorkItem:
        if target_status not in set(Status):
            raise InvalidTransition(f"invalid status: {target_status}")
        if target_status == Status.REVIEW:
            raise InvalidTransition("the local backend has no review status")
        self._record(work_id)
        self.store.set_status(work_id, target_status, blocker=blocker)
        return self.get(work_id)
