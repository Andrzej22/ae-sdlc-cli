"""WorkBackend over Linear's MCP server: Epic -> Project, Spec -> Issue.

Linear offers no conditional writes or idempotency keys, so duplicate prevention stores a stable
operation key in the description and looks it up before creating and after a lost response. Revision
checks are read-then-compare and leave a small race. Comments use an HTML-comment marker, which the
feasibility round trip verified; descriptions use a plain footer line, which needs no such guarantee.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .backend import CapabilityMissing, InvalidTransition, RevisionConflict, WorkConfigError, WorkItemNotFound
from .linear_status import BLOCKED_LABEL, CANCELED, StatusMapping, build_status_mapping
from .mcp.capabilities import invoke, is_available, operation
from .mcp.client import McpClient, ToolResponse
from .mcp.doctor import rows
from .mcp.errors import InvalidResponse, ToolFailed, TransportFailure
from .models import Status, WorkComment, WorkItem
from .remote import BLOCKER_MARKER, BLOCKER_PREFIX, COMMENT_MARKER, name_of, op_key, sort_key

MARKER_LABEL = "ae-sdlc"
_OP_PREFIX = "ae-sdlc-op: "
_FOOTER = re.compile(r"\s*(?:^[-*_]{3,}\s*)?^ae-sdlc-op:\s*([0-9a-f]{32})\s*\Z", re.MULTILINE)
_ISSUE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9]*-\d+")
_PROJECT_KEY = re.compile(r"P-[A-Za-z0-9]+-\d+")
_NOT_FOUND = re.compile(r"not found|could not find|does not exist", re.IGNORECASE)

_ISSUE_FIELDS = ["id", "title", "description", "url", "updatedAt", "status", "statusType", "labels",
                 "project", "projectId", "parentId"]
_PROJECT_FIELDS = ["id", "name", "description", "url", "updatedAt", "status"]
_PROJECT_WRITE_STATE = {Status.DRAFT: "backlog", Status.READY: "planned", Status.IN_PROGRESS: "started",
                        Status.BLOCKED: "paused", Status.DONE: "completed"}
_PROJECT_READ_STATE = {"backlog": Status.DRAFT, "planned": Status.READY, "started": Status.IN_PROGRESS,
                       "in progress": Status.IN_PROGRESS, "paused": Status.BLOCKED, "completed": Status.DONE,
                       "canceled": CANCELED}


def _with_footer(description: str, key: str | None) -> str:
    return f"{description.rstrip()}\n\n---\n\n{_OP_PREFIX}{key}" if key else description


def _split_footer(description: str) -> tuple[str, str | None]:
    match = _FOOTER.search(description)
    return (description[:match.start()], match.group(1)) if match else (description, None)


def _ref(value: Any) -> str | None:
    """Return the stable reference of a value that may be a plain string or a nested record."""
    if isinstance(value, dict):
        value = value.get("identifier") or value.get("id")
    return str(value) if value else None


def _not_found(exc: ToolFailed) -> bool:
    return bool(_NOT_FOUND.search(str(exc)))


class LinearWorkBackend:
    def __init__(self, client: McpClient, settings: Mapping[str, str]):
        if not settings.get("team"):
            raise WorkConfigError("[linear] team is not set; run ./ae-sdlc backend connect linear")
        self.client = client
        self.settings = settings
        self.team = settings["team"]
        self._mapping: StatusMapping | None = None
        self._tools: set[str] | None = None
        self._labels: set[str] = set()

    # --- provider access ---

    def _call(self, name: str, arguments: dict[str, Any], *, read: bool = False) -> ToolResponse:
        return invoke(self.client, operation("linear", name), arguments, retry_safe=read)

    def _require(self, name: str) -> None:
        if self._tools is None:
            self._tools = {tool.name for tool in self.client.list_tools()}
        op = operation("linear", name)
        if not is_available(op, self._tools):
            raise CapabilityMissing(f"Linear does not offer {op.tool} to this credential, needed for {name}")

    def _pages(self, name: str, arguments: dict[str, Any], key: str) -> list[dict]:
        found: list[dict] = []
        cursor = None
        while True:
            data = self._call(name, {**arguments, **({"cursor": cursor} if cursor else {})}, read=True).data
            found += rows(data, key, "nodes")
            following = data.get("cursor") if isinstance(data, dict) and data.get("hasNextPage") else None
            if not following or following == cursor:
                return found
            cursor = following

    @property
    def mapping(self) -> StatusMapping:
        if self._mapping is None:
            data = self._call("list_statuses", {"team": self.team}, read=True).data
            self._mapping = build_status_mapping(rows(data, "statuses", "nodes"), self.settings)
        return self._mapping

    def _label_exists(self, name: str) -> bool:
        if name in self._labels:
            return True
        data = self._call("list_labels", {"name": name, "team": self.team}, read=True).data
        if any(row.get("name") == name for row in rows(data, "labels", "nodes")):
            self._labels.add(name)
        return name in self._labels

    def _ensure_label(self, name: str) -> None:
        if self._label_exists(name):
            return
        team = self._call("read_team", {"query": self.team}, read=True).require(dict, "get_team")
        self._call("create_label", {"name": name, "teamId": team.get("id")})
        self._labels.add(name)

    # --- normalization ---

    def _issue_item(self, row: dict) -> WorkItem:
        key = row.get("identifier") or row.get("id")
        if not key or "description" not in row:
            raise InvalidResponse("Linear returned an issue without an identifier or description")
        status = row.get("status")
        state = name_of(status)
        state_type = row.get("statusType") or (status.get("type") if isinstance(status, dict) else None)
        labels = [name_of(label) for label in row.get("labels") or []]
        description, op = _split_footer(row["description"] or "")
        return WorkItem(
            id=str(key), kind="spec", title=row.get("title"), description=description,
            status=self.mapping.canonical(state, state_type, labels),
            parent_id=_ref(row.get("projectId")) or _ref(row.get("project")),
            revision=row.get("updatedAt"), url=row.get("url"),
            metadata={"state": state, "labels": labels, "op_key": op, "managed": MARKER_LABEL in labels},
        )

    def _project_item(self, row: dict) -> WorkItem:
        key = row.get("identifier") or row.get("id")
        text = row["description"] if "description" in row else row.get("content", ...)
        if not key or text is ...:
            raise InvalidResponse("Linear returned a project without an identifier or description")
        status = row.get("status")
        state = (status.get("type") or status.get("name") or "") if isinstance(status, dict) else str(status or "")
        description, op = _split_footer(text or "")
        return WorkItem(
            id=str(key), kind="epic", title=row.get("name"), description=description,
            status=_PROJECT_READ_STATE.get(state.lower(), state),
            revision=row.get("updatedAt"), url=row.get("url"),
            metadata={"state": state, "op_key": op, "managed": op is not None},
        )

    def _comment(self, row: dict, work_id: str) -> WorkComment:
        return WorkComment(id=str(row.get("id")), work_item_id=work_id, author=name_of(row.get("author")),
                           body=COMMENT_MARKER.sub("", row.get("body") or "").strip(),
                           created_at=str(row.get("createdAt") or ""), updated_at=row.get("updatedAt"))

    # --- reads ---

    def _read_issue(self, work_id: str) -> WorkItem:
        arguments = {"id": work_id, "fields": [*_ISSUE_FIELDS, "relations"]}
        try:
            row = self._call("read_issue", arguments, read=True).require(dict, "get_issue")
        except ToolFailed as exc:
            if _not_found(exc):
                raise WorkItemNotFound(f"unknown work item: {work_id}") from None
            raise
        relations = row.get("relations")
        if not isinstance(relations, dict):
            raise InvalidResponse(f"Linear returned {work_id} without its relations")
        item = self._issue_item(row)
        item.dependencies = [d for d in (_ref(r) for r in relations.get("blockedBy") or []) if d]
        item.related_ids = [d for d in (_ref(r) for r in relations.get("relatedTo") or []) if d]
        return item

    def _read_project(self, work_id: str) -> WorkItem:
        try:
            return self._project_item(self._call("read_epic", {"query": work_id}, read=True).require(dict, "get_project"))
        except ToolFailed as exc:
            if _not_found(exc):
                raise WorkItemNotFound(f"unknown work item: {work_id}") from None
            raise

    def _fetch(self, work_id: str) -> WorkItem:
        """Read one item without the blocker, which costs an extra call."""
        if _PROJECT_KEY.fullmatch(work_id):
            return self._read_project(work_id)
        try:
            return self._read_issue(work_id)
        except WorkItemNotFound:
            if _ISSUE_KEY.fullmatch(work_id):
                raise
            return self._read_project(work_id)  # a UUID or slug can only be told apart by trying

    def _comment_rows(self, item: WorkItem) -> list[dict]:
        parent = {"projectId" if item.kind == "epic" else "issueId": item.id}
        found = self._pages("read_comments", {**parent, "limit": 250, "orderBy": "createdAt"}, "comments")
        return sorted(found, key=lambda row: str(row.get("createdAt") or ""))

    def _with_blocker(self, item: WorkItem) -> WorkItem:
        if item.status == Status.BLOCKED:
            item.blocker = self._latest_blocker(item)
        return item

    def _latest_blocker(self, item: WorkItem) -> str | None:
        for row in reversed(self._comment_rows(item)):
            body = row.get("body") or ""
            if BLOCKER_MARKER in body:
                return COMMENT_MARKER.sub("", body).strip().removeprefix(BLOCKER_PREFIX)
        return None

    def get(self, work_id: str) -> WorkItem:
        return self._with_blocker(self._fetch(work_id))

    def _managed_projects(self) -> list[WorkItem]:
        found = self._pages("list_epics", {"team": self.team, "limit": 50, "fields": _PROJECT_FIELDS}, "projects")
        return [item for item in map(self._project_item, found) if item.metadata["managed"]]

    def _managed_issues(self) -> list[WorkItem]:
        arguments = {"team": self.team, "label": MARKER_LABEL, "limit": 250, "fields": _ISSUE_FIELDS}
        return [self._issue_item(row) for row in self._pages("search_issues", arguments, "issues")]

    def list_items(self) -> list[WorkItem]:
        """Return managed projects and issues. Dependencies stay empty: Linear's list omits relations."""
        issues = self._managed_issues() if self._label_exists(MARKER_LABEL) else []
        return [self._with_blocker(item) for item in sorted([*self._managed_projects(), *issues], key=sort_key)]

    def list_specs(self, scope: str, status: str | None = None) -> list[WorkItem]:
        try:
            target = self._fetch(scope)
        except WorkItemNotFound:
            return []
        if target.kind == "spec":
            return [self._with_blocker(target)] if status is None or target.status == status else []
        arguments = {"project": target.id, "limit": 250, "fields": _ISSUE_FIELDS}
        listed = [self._issue_item(row) for row in self._pages("search_issues", arguments, "issues")]
        chosen = [i for i in listed if i.metadata["managed"] and (status is None or i.status == status)]
        # Dependencies decide eligibility, and only a single-issue read returns them.
        return sorted((self.get(i.id) for i in chosen), key=sort_key)

    def search(self, query: str, limit: int = 20) -> list[WorkItem]:
        """Find existing issues and projects by text, managed or not. Dependencies are not loaded."""
        issues = self._call("search_issues", {"team": self.team, "query": query, "limit": limit,
                                              "fields": _ISSUE_FIELDS}, read=True).data
        projects = self._call("list_epics", {"team": self.team, "query": query, "limit": min(limit, 50),
                                             "fields": _PROJECT_FIELDS}, read=True).data
        return [*map(self._project_item, rows(projects, "projects", "nodes")),
                *map(self._issue_item, rows(issues, "issues", "nodes"))]

    def get_dependencies(self, work_id: str) -> list[WorkItem]:
        return self._existing(self._fetch(work_id).dependencies)

    def get_related(self, work_id: str) -> list[WorkItem]:
        return self._existing(self._fetch(work_id).related_ids)

    def _existing(self, work_ids: list[str]) -> list[WorkItem]:
        found = []
        for work_id in work_ids:
            try:
                found.append(self.get(work_id))
            except WorkItemNotFound:
                pass
        return found

    # --- writes ---

    def _find_created(self, kind: str, key: str) -> WorkItem | None:
        """Scan managed work rather than text search, whose index may lag behind a create."""
        candidates = self._managed_projects() if kind == "epic" else self._managed_issues()
        match = next((i for i in candidates if i.metadata["op_key"] == key and i.status != CANCELED), None)
        return self.get(match.id) if match else None

    def create(self, item: WorkItem, idempotency_key: str | None = None) -> WorkItem:
        """Create the item, or return the one this key already produced with metadata["reused"] set.

        Without a key, the same title under the same parent counts as the same work.
        """
        if item.kind not in {"epic", "spec"}:
            raise ValueError(f"invalid kind: {item.kind}")
        if item.status not in set(Status):
            raise InvalidTransition(f"invalid status: {item.status}")
        if not item.title:
            raise ValueError("a Linear work item needs a title")
        self._require("create_epic" if item.kind == "epic" else "create_issue")
        if item.kind == "spec" and item.dependencies:
            self._require("create_dependencies")
        identity = idempotency_key or f"{item.kind}\0{item.parent_id}\0{item.title.strip().lower()}"
        key = op_key(identity)
        self._ensure_label(MARKER_LABEL)
        if item.kind == "spec" and item.status == Status.BLOCKED and self.mapping.uses_blocked_label:
            self._ensure_label(BLOCKED_LABEL)

        existing = self._find_created(item.kind, key)
        if existing is None:
            try:
                created = self._save_new_epic(item, key) if item.kind == "epic" else self._save_new_issue(item, key)
                return self.get(created)
            except TransportFailure:
                # The request may have been applied with only the response lost.
                existing = self._find_created(item.kind, key)
                if existing is None:
                    raise
        existing.metadata["reused"] = True
        return existing

    def _new_id(self, response: ToolResponse, tool: str) -> str:
        row = response.require(dict, tool)
        created = row.get("identifier") or row.get("id")
        if not created:
            raise InvalidResponse(f"{tool}: the response has no identifier")
        return str(created)

    def _save_new_issue(self, item: WorkItem, key: str) -> str:
        labels = [MARKER_LABEL]
        if item.status == Status.BLOCKED and self.mapping.uses_blocked_label:
            labels.append(BLOCKED_LABEL)
        arguments: dict[str, Any] = {"team": self.team, "title": item.title, "labels": labels,
                                     "description": _with_footer(item.description, key),
                                     "state": self.mapping.state_for(item.status)}
        if item.parent_id:
            arguments["project"] = item.parent_id
        if item.dependencies:
            arguments["blockedBy"] = item.dependencies
        return self._new_id(self._call("create_issue", arguments), "save_issue")

    def _save_new_epic(self, item: WorkItem, key: str) -> str:
        arguments = {"name": item.title, "description": _with_footer(item.description, key),
                     "addTeams": [self.team], "state": _PROJECT_WRITE_STATE[Status(item.status)]}
        return self._new_id(self._call("create_epic", arguments), "save_project")

    def update(self, item: WorkItem, expected_revision: str | None = None) -> WorkItem:
        current = self._fetch(item.id)
        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflict(f"{item.id} changed since revision {expected_revision}")
        description = _with_footer(item.description, current.metadata["op_key"])
        if current.kind == "epic":
            self._require("create_epic")  # save_project both creates and updates
            self._call("create_epic", {"id": current.id, "name": item.title, "description": description})
            return self.get(current.id)
        arguments: dict[str, Any] = {"id": current.id, "title": item.title, "description": description,
                                     "project": item.parent_id}
        added = [d for d in item.dependencies if d not in current.dependencies]
        removed = [d for d in current.dependencies if d not in item.dependencies]
        if added or removed:
            self._require("create_dependencies")
        if added:
            arguments["blockedBy"] = added
        if removed:
            arguments["removeBlockedBy"] = removed
        self._call("update_issue", arguments)
        return self.get(current.id)

    def get_comments(self, work_id: str, limit: int | None = None) -> list[WorkComment]:
        """Return the newest `limit` comments, oldest first."""
        item = self._fetch(work_id)
        found = self._comment_rows(item)
        if limit is not None:
            found = found[-limit:] if limit > 0 else []
        return [self._comment(row, item.id) for row in found]

    def _find_comment(self, item: WorkItem, marker: str) -> dict | None:
        return next((row for row in self._comment_rows(item) if marker in (row.get("body") or "")), None)

    def _post_comment(self, item: WorkItem, body: str) -> dict:
        parent = {"projectId" if item.kind == "epic" else "issueId": item.id}
        return self._call("add_comment", {**parent, "body": body}).require(dict, "save_comment")

    def add_comment(self, work_id: str, text: str, dedupe_key: str | None = None) -> WorkComment:
        item = self._fetch(work_id)
        if dedupe_key is None:
            return self._comment(self._post_comment(item, text), item.id)
        marker = f"<!-- ae-sdlc-op:{op_key(dedupe_key)} -->"
        row = self._find_comment(item, marker)
        if row is None:
            try:
                row = self._post_comment(item, f"{text}\n\n{marker}")
            except TransportFailure:
                row = self._find_comment(item, marker)
                if row is None:
                    raise
        return self._comment(row, item.id)

    def transition(self, work_id: str, target_status: str, blocker: str | None = None) -> WorkItem:
        """Apply the status and confirm it by reading it back. Retrying after a failure is safe."""
        if target_status not in set(Status):
            raise InvalidTransition(f"invalid status: {target_status}")
        item = self._fetch(work_id)
        try:
            if item.kind == "epic":
                self._require("create_epic")
                self._call("create_epic", {"id": item.id, "state": _PROJECT_WRITE_STATE[Status(target_status)]})
            else:
                self._call("transition_issue", self._transition_arguments(item, target_status))
        except ToolFailed as exc:
            raise InvalidTransition(f"{item.id} cannot move to {target_status}: {exc}") from exc
        confirmed = self._fetch(item.id)
        if confirmed.status != target_status:
            raise InvalidTransition(f"{item.id} is {confirmed.status} after requesting {target_status}")
        if target_status == Status.BLOCKED:
            if blocker and self._latest_blocker(confirmed) != blocker:
                self._post_comment(confirmed, f"{BLOCKER_PREFIX}{blocker}\n\n{BLOCKER_MARKER}")
            confirmed.blocker = blocker or self._latest_blocker(confirmed)
        return confirmed

    def _transition_arguments(self, item: WorkItem, target: str) -> dict[str, Any]:
        arguments: dict[str, Any] = {"id": item.id, "state": self.mapping.state_for(target)}
        has_label = BLOCKED_LABEL in item.metadata["labels"]
        if target == Status.BLOCKED and self.mapping.uses_blocked_label:
            if not has_label:
                self._ensure_label(BLOCKED_LABEL)
                arguments["addLabels"] = [BLOCKED_LABEL]
        elif has_label:
            arguments["removeLabels"] = [BLOCKED_LABEL]
        return arguments
