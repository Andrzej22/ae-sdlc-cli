"""WorkBackend over Atlassian's MCP server: Epic -> Jira Epic, Spec -> a configured issue type.

The server converts Markdown to and from ADF, so no converter lives here. It also normalizes text, so
the operation key that prevents duplicates is a label rather than part of the description. Jira offers
no conditional writes and its search can trail a create by moments: revision checks are
read-then-compare, and duplicate prevention leaves a small race.
"""
from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from .backend import CapabilityMissing, InvalidTransition, RevisionConflict, WorkConfigError, WorkItemNotFound
from .jira_status import PENDING_LABEL, StatusPolicy
from .mcp.capabilities import DIRECT, invoke, operation
from .mcp.client import McpClient, ToolResponse
from .mcp.doctor import rows
from .mcp.errors import InvalidResponse, ToolFailed, TransportFailure
from .models import Status, WorkComment, WorkItem
from .remote import BLOCKER_MARKER, BLOCKER_PREFIX, COMMENT_MARKER, name_of, op_key, sort_key

MARKER_LABEL = "ae-sdlc"
_OP_LABEL = "ae-sdlc-op-"
_NOT_FOUND = re.compile(r"not found|does not exist|no issue", re.IGNORECASE)
_LINK_TYPE = "Blocks"
_PAGE = 100
_CONFIRM_ATTEMPTS = 3
_CONFIRM_DELAY_SECONDS = 1.5
_READ_FIELDS = ["summary", "description", "status", "labels", "issuetype", "parent", "updated"]
_SUPPLIED_FIELDS = {"summary", "issuetype", "project", "reporter", "description", "parent", "labels"}


def _key(value: Any) -> str | None:
    return str(value["key"]) if isinstance(value, dict) and value.get("key") else None


def _jql_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unwrap(data: Any) -> Any:
    """Remove the {"data": ...} envelope Atlassian puts around every result."""
    return data["data"] if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)) else data


def _not_found(exc: ToolFailed) -> bool:
    return bool(_NOT_FOUND.search(str(exc)))


class JiraWorkBackend:
    supports_review = False  # a Jira workflow's own review status is shown as it is, never mapped

    def __init__(self, client: McpClient, settings: Mapping[str, str]):
        missing = [key for key in ("cloud_id", "project_key") if not settings.get(key)]
        if missing:
            raise WorkConfigError(f"[jira] {', '.join(missing)} not set; run ae-sdlc backend connect jira")
        self.client = client
        self.settings = settings
        self.cloud_id = settings["cloud_id"]
        self.project = settings["project_key"]
        self.spec_type = settings.get("issue_type_spec") or "Task"
        self.epic_type = settings.get("issue_type_epic") or "Epic"
        self.policy = StatusPolicy(settings)
        self._tools: set[str] | None = None
        self._types: list[dict] | None = None
        self._checked_types: set[str] = set()

    # --- provider access ---

    def _call(self, name: str, arguments: dict[str, Any], *, read: bool = False) -> ToolResponse:
        op = operation("jira", name)
        if op.route == DIRECT:
            arguments = {"cloudId": self.cloud_id, **arguments}
        response = invoke(self.client, op, arguments, cloud_id=self.cloud_id, retry_safe=read)
        return ToolResponse(response.text, _unwrap(response.data))

    def _require(self, name: str) -> None:
        if self._tools is None:
            self._tools = {tool.name for tool in self.client.list_tools()}
        op = operation("jira", name)
        # A deferred tool is absent from this connection's list; only its gateway can be checked here.
        if (op.route if op.route != DIRECT else op.tool) not in self._tools:
            raise CapabilityMissing(f"Jira does not offer {op.tool} to this credential, needed for {name}")

    def _issue_type(self, name: str, *, epic: bool) -> dict:
        """Resolve a configured type against this project, whose hierarchy may differ from others."""
        if self._types is None:
            data = self._call("list_issue_types", {"projectIdOrKey": self.project, "maxResults": 200}, read=True).data
            self._types = rows(data, "issueTypes", "values")
        found = next((t for t in self._types if str(t.get("name", "")).lower() == name.lower()), None)
        setting = "issue_type_epic" if epic else "issue_type_spec"
        if found is None:
            names = ", ".join(str(t.get("name")) for t in self._types) or "none"
            raise CapabilityMissing(f"project {self.project} has no issue type {name!r}; "
                                    f"set {setting} under [jira] to one of: {names}")
        level = found.get("hierarchyLevel")
        if (epic and level not in (None, 1)) or (not epic and (found.get("subtask") or level == 1)):
            raise CapabilityMissing(f"issue type {name!r} cannot hold {'specs' if epic else 'a spec'} "
                                    f"in {self.project}; change {setting} under [jira]")
        return found

    def _check_creatable(self, name: str, *, epic: bool) -> None:
        """Report fields only a person can supply before anything is written."""
        if name in self._checked_types:
            return
        arguments = {"projectIdOrKey": self.project, "issueTypeId": str(self._issue_type(name, epic=epic).get("id")),
                     "maxResults": 200}
        fields = rows(self._call("read_type_fields", arguments, read=True).data, "fields", "values")
        missing = [str(f.get("name") or f.get("fieldId")) for f in fields
                   if f.get("required") and not f.get("hasDefaultValue")
                   and str(f.get("fieldId") or f.get("key")) not in _SUPPLIED_FIELDS]
        if missing:
            raise CapabilityMissing(f"Jira requires fields ae-sdlc cannot supply for {name} in {self.project}: "
                                    f"{', '.join(missing)}. Make them optional or give them defaults.")
        self._checked_types.add(name)

    # --- normalization ---

    def _item(self, row: dict) -> WorkItem:
        key = _key(row)
        fields = row["fields"] if isinstance(row.get("fields"), dict) else row
        description = fields.get("description")
        if not key or not isinstance(description, (str, type(None))):
            raise InvalidResponse("Jira returned an issue without a key or a text description")
        status = fields.get("status")
        state = name_of(status)
        labels = [str(label) for label in fields.get("labels") or []]
        epic = name_of(fields.get("issuetype")).lower() == self.epic_type.lower()
        op = next((label.removeprefix(_OP_LABEL) for label in labels if label.startswith(_OP_LABEL)), None)
        return WorkItem(
            id=key, kind="epic" if epic else "spec", title=fields.get("summary"), description=description or "",
            status=self.policy.canonical(state, labels), parent_id=_key(fields.get("parent")),
            revision=fields.get("updated"), url=row.get("url") or row.get("webUrl"),
            metadata={"state": state, "labels": labels, "op_key": op,
                      "managed": MARKER_LABEL in labels, "content_format": row.get("appliedContentFormat")},
        )

    def _comment(self, row: dict, work_id: str) -> WorkComment:
        return WorkComment(id=str(row.get("id") or row.get("commentId")), work_item_id=work_id, author=name_of(row.get("author")),
                           body=COMMENT_MARKER.sub("", str(row.get("body") or "")).strip(),
                           created_at=str(row.get("created") or ""), updated_at=row.get("updated"))

    # --- reads ---

    def _fetch(self, work_id: str) -> WorkItem:
        """Read one item, with links, without the blocker, which costs an extra call."""
        try:
            row = self._call("read_issue", {"issueIdOrKey": work_id, "view": "evidence"}, read=True).require(dict, "getJiraIssue")
        except ToolFailed as exc:
            if _not_found(exc):
                raise WorkItemNotFound(f"unknown work item: {work_id}") from None
            raise
        fields = row["fields"] if isinstance(row.get("fields"), dict) else row
        if not isinstance(fields.get("issuelinks"), list):
            raise InvalidResponse(f"Jira returned {work_id} without its issue links")
        item = self._item(row)
        for link in fields["issuelinks"]:
            kind = link.get("type") or {}
            blocking = name_of(kind).lower() == _LINK_TYPE.lower() or "blocked by" in str(kind.get("inward", "")).lower()
            inward, outward = _key(link.get("inwardIssue")), _key(link.get("outwardIssue"))
            if blocking and inward:  # shown from this issue's side: the inward issue blocks it
                item.dependencies.append(inward)
            elif not blocking and (inward or outward):
                item.related_ids.append(inward or outward)
        return item

    def _search(self, jql: str) -> list[dict]:
        found: list[dict] = []
        token = None
        while True:
            arguments = {"jql": jql, "maxResults": _PAGE, "fields": _READ_FIELDS, "view": "evidence",
                         **({"nextPageToken": token} if token else {})}
            data = self._call("search_issues", arguments, read=True).data
            found += rows(data, "issues", "nodes")
            following = data.get("nextPageToken") if isinstance(data, dict) else None
            if not following or following == token:
                return found
            token = following

    def _comment_rows(self, work_id: str) -> list[dict]:
        found: list[dict] = []
        while True:
            data = self._call("read_comments", {"issueIdOrKey": work_id, "startAt": len(found), "maxResults": _PAGE,
                                                "orderBy": "created"}, read=True).data
            page = rows(data, "comments", "values")
            found += page
            total = data.get("total") if isinstance(data, dict) else None
            if not page or (isinstance(total, int) and len(found) >= total):
                return found

    def _latest_blocker(self, work_id: str) -> str | None:
        for row in reversed(self._comment_rows(work_id)):
            body = str(row.get("body") or "")
            if BLOCKER_MARKER in body:
                return COMMENT_MARKER.sub("", body).strip().removeprefix(BLOCKER_PREFIX)
        return None

    def _with_blocker(self, item: WorkItem) -> WorkItem:
        if item.status == Status.BLOCKED:
            item.blocker = self._latest_blocker(item.id)
        return item

    def get(self, work_id: str) -> WorkItem:
        return self._with_blocker(self._fetch(work_id))

    def list_items(self) -> list[WorkItem]:
        """Return managed work in the project. Search omits links and parents, so those stay empty."""
        found = self._search(f'project = "{self.project}" AND labels = "{MARKER_LABEL}" ORDER BY created ASC')
        return [self._with_blocker(item) for item in sorted(map(self._item, found), key=sort_key)]

    def list_specs(self, scope: str, status: str | None = None) -> list[WorkItem]:
        try:
            target = self._fetch(scope)
        except WorkItemNotFound:
            return []
        if target.kind == "spec":
            return [self._with_blocker(target)] if status is None or target.status == status else []
        children = map(self._item, self._search(f'parent = "{target.id}" ORDER BY created ASC'))
        chosen = [i for i in children if i.kind == "spec" and i.metadata["managed"] and (status is None or i.status == status)]
        # Dependencies decide eligibility, and only a single-issue read returns them.
        return sorted((self.get(i.id) for i in chosen), key=sort_key)

    def search(self, query: str, limit: int = 20) -> list[WorkItem]:
        """Find existing issues by text, managed or not. Dependencies are not loaded."""
        arguments = {"jql": f'project = "{self.project}" AND text ~ "{_jql_text(query)}" ORDER BY updated DESC',
                     "maxResults": min(limit, _PAGE), "fields": _READ_FIELDS, "view": "evidence"}
        return [self._item(row) for row in rows(self._call("search_issues", arguments, read=True).data, "issues", "nodes")]

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

    def _find_created(self, key: str) -> WorkItem | None:
        jql = f'project = "{self.project}" AND labels = "{_OP_LABEL}{key}" ORDER BY created ASC'
        match = next(iter(map(self._item, self._search(jql))), None)
        return self.get(match.id) if match else None

    def create(self, item: WorkItem, idempotency_key: str | None = None) -> WorkItem:
        """Create the item, or return the one this key already produced with metadata["reused"] set.

        Without a key, the same title under the same parent counts as the same work.
        """
        if item.kind not in {"epic", "spec"}:
            raise ValueError(f"invalid kind: {item.kind}")
        if item.status not in set(Status) or item.status == Status.REVIEW:
            raise InvalidTransition(f"invalid status: {item.status}")
        if not item.title:
            raise ValueError("a Jira work item needs a title")
        epic = item.kind == "epic"
        if item.dependencies:
            self._require("create_dependencies")
        issue_type = self.epic_type if epic else self.spec_type
        self._check_creatable(issue_type, epic=epic)
        key = op_key(idempotency_key or f"{item.kind}\0{item.parent_id}\0{item.title.strip().lower()}")

        existing = self._find_created(key)
        reused = existing is not None
        if existing is None:
            try:
                existing = self.get(self._save_new(item, issue_type, key))
            except TransportFailure:
                # The request may have been applied with only the response lost.
                existing = self._find_created(key)
                if existing is None:
                    raise
                reused = True
        return self._finish_create(existing, item, reused)

    def _save_new(self, item: WorkItem, issue_type: str, key: str) -> str:
        labels = [MARKER_LABEL, f"{_OP_LABEL}{key}", PENDING_LABEL]
        arguments: dict[str, Any] = {"projectKey": self.project, "summary": item.title, "issueType": issue_type,
                                     "description": item.description, "labels": labels}
        if item.parent_id:
            arguments["parent"] = item.parent_id
        created = _key(self._call("create_issue", arguments).require(dict, "createJiraIssue"))
        if not created:
            raise InvalidResponse("createJiraIssue: the response has no issue key")
        return created

    def _finish_create(self, current: WorkItem, item: WorkItem, reused: bool) -> WorkItem:
        """Apply what createJiraIssue cannot do. A create interrupted here is completed by its retry."""
        for dependency in item.dependencies:
            if dependency not in current.dependencies:
                self._link(dependency, current.id)
        if PENDING_LABEL in current.metadata["labels"]:
            self._apply_status(current, item.status)
        done = self.get(current.id)
        if reused:
            done.metadata["reused"] = True
        return done

    def _link(self, blocker: str, blocked: str) -> None:
        self._call("create_dependencies", {"linkType": _LINK_TYPE, "inwardIssue": blocker, "outwardIssue": blocked})

    def update(self, item: WorkItem, expected_revision: str | None = None) -> WorkItem:
        current = self._fetch(item.id)
        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflict(f"{item.id} changed since revision {expected_revision}")
        if current.metadata["content_format"] == "html":
            raise CapabilityMissing(f"{item.id} holds content Markdown cannot represent; a Markdown update would lose it")
        added = [d for d in item.dependencies if d not in current.dependencies]
        removed = [d for d in current.dependencies if d not in item.dependencies]
        if removed:
            raise CapabilityMissing(f"Jira's MCP server cannot delete issue links; remove {', '.join(removed)} "
                                    f"from {item.id} in Jira")
        if added:
            self._require("create_dependencies")
        fields: dict[str, Any] = {"summary": item.title, "description": item.description}
        if item.parent_id != current.parent_id:
            fields["parent"] = {"key": item.parent_id} if item.parent_id else None
        self._call("update_issue", {"issueIdOrKey": current.id, "fields": fields})
        for dependency in added:
            self._link(dependency, current.id)
        return self.get(current.id)

    def get_comments(self, work_id: str, limit: int | None = None) -> list[WorkComment]:
        """Return the newest `limit` comments, oldest first."""
        item = self._fetch(work_id)
        found = self._comment_rows(item.id)
        if limit is not None:
            found = found[-limit:] if limit > 0 else []
        return [self._comment(row, item.id) for row in found]

    def _find_comment(self, work_id: str, marker: str) -> dict | None:
        return next((row for row in self._comment_rows(work_id) if marker in str(row.get("body") or "")), None)

    def _post_comment(self, work_id: str, body: str) -> dict:
        arguments = {"issueIdOrKey": work_id, "commentBody": body}
        return self._call("add_comment", arguments).require(dict, "addOrEditJiraIssueComment")

    def add_comment(self, work_id: str, text: str, dedupe_key: str | None = None) -> WorkComment:
        item = self._fetch(work_id)
        if dedupe_key is None:
            return self._comment(self._post_comment(item.id, text), item.id)
        marker = f"<!-- ae-sdlc-op:{op_key(dedupe_key)} -->"
        row = self._find_comment(item.id, marker)
        if row is None:
            try:
                row = self._post_comment(item.id, f"{text}\n\n{marker}")
            except TransportFailure:
                row = self._find_comment(item.id, marker)
                if row is None:
                    raise
        return self._comment(row, item.id)

    def _offered(self, work_id: str) -> list[dict]:
        return rows(self._call("list_transitions", {"issueIdOrKey": work_id}, read=True).data, "transitions")

    def _apply_status(self, item: WorkItem, target: str) -> None:
        """Move first, then relabel: a failed move leaves the labels and the status as they were."""
        chosen = self.policy.choose_transition(target, item.metadata["state"], lambda: self._offered(item.id))
        if chosen is not None:
            self._call("transition_issue", {"issueIdOrKey": item.id, "transitionId": str(chosen.get("id"))})
        labels = self.policy.labels_after(target, item.metadata["labels"])
        if set(labels) != set(item.metadata["labels"]):
            self._call("update_issue", {"issueIdOrKey": item.id, "fields": {"labels": labels}})

    def _apply_until_confirmed(self, item: WorkItem, target: str) -> WorkItem:
        """Jira has been seen to answer "transitioned successfully" and leave the status unchanged."""
        for attempt in range(_CONFIRM_ATTEMPTS):
            if attempt:
                time.sleep(_CONFIRM_DELAY_SECONDS)
            self._apply_status(item, target)
            item = self._fetch(item.id)
            if item.status == target:
                return item
        raise InvalidTransition(f"{item.id} is {item.status} after requesting {target}")

    def transition(self, work_id: str, target_status: str, blocker: str | None = None) -> WorkItem:
        """Apply the status and confirm it by reading it back. Retrying after a failure is safe."""
        if target_status not in set(Status) or target_status == Status.REVIEW:
            raise InvalidTransition(f"invalid status: {target_status}")
        item = self._fetch(work_id)
        try:
            confirmed = self._apply_until_confirmed(item, target_status)
        except ToolFailed as exc:
            raise InvalidTransition(f"{item.id} cannot move to {target_status}: {exc}") from exc
        if target_status == Status.BLOCKED:
            latest = self._latest_blocker(item.id)
            if blocker and latest != blocker:
                self._post_comment(item.id, f"{BLOCKER_PREFIX}{blocker}\n\n{BLOCKER_MARKER}")
                latest = blocker
            confirmed.blocker = latest
        return confirmed
