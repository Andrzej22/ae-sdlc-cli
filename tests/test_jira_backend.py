"""PR 4: JiraWorkBackend against a stateful fake that follows the recorded Atlassian tool schemas."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from ae_sdlc.work.backend import (CapabilityMissing, InvalidTransition, RevisionConflict, WorkBackend, WorkConfigError,
                                  WorkItemNotFound)
from ae_sdlc.work.jira import JiraWorkBackend
from ae_sdlc.work.jira_status import (BLOCKED_LABEL, PENDING_LABEL, READY_LABEL, StatusPolicy, check_mapping,
                                      propose_mapping)
from ae_sdlc.work.mcp.client import ToolResponse
from ae_sdlc.work.mcp.errors import PermissionDenied, TransportFailure, failure_from_tool
from ae_sdlc.work.models import Status, WorkItem
from ae_sdlc.work.service import WorkService

S = {"Backlog": "new", "To Do": "new", "In Progress": "indeterminate", "In Review": "indeterminate", "Blocked": "indeterminate",
     "Done": "done", "Rejected": "done", "Ready": "new"}
NO_BLOCKED = ["To Do", "In Progress", "In Review", "Done", "Rejected"]
WITH_BLOCKED = ["To Do", "In Progress", "Done", "Blocked"]
SPEC = "## Goal\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('Привіт')\n```\n\n- [ ] Accept\n"
LABEL_BLOCKED = {"cloud_id": "cloud", "project_key": "KAN", "status_draft": "To Do", "status_ready": "To Do",
                 "status_in_progress": "In Progress", "status_done": "Done"}
SETTINGS = {**LABEL_BLOCKED, "status_blocked": "Blocked"}
TYPES = [{"id": "1", "name": "Epic", "hierarchyLevel": 1}, {"id": "2", "name": "Task", "hierarchyLevel": 0},
         {"id": "3", "name": "Story", "hierarchyLevel": 0}, {"id": "4", "name": "Sub-task", "hierarchyLevel": -1, "subtask": True}]


def status(name):
    return {"name": name, "statusCategory": {"key": S[name]}}


class FakeJira:
    GATEWAY = {"executeRead", "executeWrite"}

    def __init__(self, statuses=WITH_BLOCKED, page=2, routes=None, required=()):
        self.statuses, self.page, self.routes, self.required = statuses, page, routes, required
        self.issues, self.comments, self.links = {}, {}, []
        self.calls, self.lose_response_of, self.denied = [], set(), set()
        self.clock = self.seq = 0
        self.hide = set()

    def list_tools(self):
        visible = {n[5:] for n in dir(self) if n.startswith("tool_")}
        return [SimpleNamespace(name=n) for n in visible - self.hide]

    def call(self, tool, arguments, *, retry_safe=False):
        self.calls.append((tool, copy.deepcopy(arguments)))
        if tool in self.GATEWAY:
            assert arguments["cloudId"] == "cloud"
            name, inner = arguments["name"], dict(arguments["inputs"])
        else:
            assert tool in {"getJiraIssue", "searchJiraIssuesUsingJql", "createJiraIssue", "editJiraIssue",
                            "transitionJiraIssue", "listJiraIssueComments", "addOrEditJiraIssueComment"}
            assert arguments["cloudId"] == "cloud"
            name, inner = tool, {k: v for k, v in arguments.items() if k != "cloudId"}
        if name in self.denied:
            raise failure_from_tool(name, json.dumps({"status": 403, "message": "nope"}), False)
        data = getattr(self, f"tool_{name}")(**inner)
        failure = failure_from_tool(name, json.dumps(data), False)
        if failure:
            raise failure
        if name in self.lose_response_of:
            self.lose_response_of.discard(name)
            raise TransportFailure("connection dropped after the server applied the request")
        return ToolResponse("", {"data": data})  # the envelope every real Atlassian result has

    def used(self, name):
        direct = [a for tool, a in self.calls if tool == name]
        return direct + [a["inputs"] for tool, a in self.calls if tool in self.GATEWAY and a["name"] == name]

    def _tick(self):
        self.clock += 1
        return f"2026-11-09T00:00:{self.clock:02d}.000+0000"

    def _issue(self, key):
        return self.issues.get(key)

    def _fields(self, i):
        return {"summary": i["summary"], "description": i["description"], "status": status(i["status"]),
                "labels": list(i["labels"]), "issuetype": {"name": i["type"]}, "updated": i["updated"]}

    def _missing(self, key):
        return {"error": "NOT_FOUND", "status": 404, "message": f"Issue {key} does not exist or you lack permission."}

    def tool_getJiraIssue(self, issueIdOrKey, view):
        assert view == "evidence"
        i = self._issue(issueIdOrKey)
        if i is None:
            return self._missing(issueIdOrKey)
        fields = self._fields(i)
        if i["parent"]:
            fields["parent"] = {"key": i["parent"]}
        fields["issuelinks"] = []
        kinds = {"Blocks": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                 "Relates": {"name": "Relates", "inward": "relates to", "outward": "relates to"}}
        for kind, inward, outward in self.links:
            if outward == issueIdOrKey:
                fields["issuelinks"].append({"type": kinds[kind], "inwardIssue": {"key": inward}})
            elif inward == issueIdOrKey:
                fields["issuelinks"].append({"type": kinds[kind], "outwardIssue": {"key": outward}})
        return {"key": i["key"], "url": f"https://x/browse/{i['key']}", "fields": fields}

    def tool_searchJiraIssuesUsingJql(self, jql, maxResults, fields, view, nextPageToken=None):
        def matches(i):
            for clause in jql.split(" ORDER BY")[0].split(" AND "):
                name, _, value = clause.partition(" ")
                value = value.split('"')[1].replace('\\"', '"') if '"' in value else ""
                if name == "project" and value != "KAN":
                    return False
                if name == "labels" and value not in i["labels"]:
                    return False
                if name == "parent" and i["parent"] != value:
                    return False
                if name == "text" and value.lower() not in (i["summary"] + i["description"]).lower():
                    return False
            return True
        found = [i for i in self.issues.values() if matches(i)]
        start = int(nextPageToken or 0)
        end = start + min(maxResults, self.page)
        page = found[start:end]
        more = end < len(found)
        return {"issues": [{"key": i["key"], "fields": self._fields(i)} for i in page], "isLast": not more,
                **({"nextPageToken": str(end)} if more else {})}

    def tool_createJiraIssue(self, projectKey, summary, issueType, description, labels, parent=None):
        if parent and parent not in self.issues:
            return {"error": "BAD", "status": 400, "message": f"parent {parent} is invalid"}
        self.seq += 1
        key = f"{projectKey}-{self.seq}"
        self.issues[key] = {"key": key, "summary": summary, "description": description, "labels": list(labels),
                            "type": issueType, "parent": parent, "status": "To Do", "updated": self._tick()}
        self.comments[key] = []
        return {"key": key, "id": str(10000 + self.seq)}

    def tool_editJiraIssue(self, issueIdOrKey, fields):
        i = self.issues[issueIdOrKey]
        for name, value in fields.items():
            if name == "parent":
                i["parent"] = value["key"] if value else None
            else:
                i[name] = value
        i["updated"] = self._tick()
        return {"key": issueIdOrKey}

    def _targets(self, i):
        if self.routes and i["status"] in self.routes:
            return self.routes[i["status"]]
        return [s for s in self.statuses if s != i["status"]]

    def tool_listJiraIssueTransitions(self, issueIdOrKey):
        return {"transitions": [{"id": f"t-{s}", "name": f"Go {s}", "to": status(s)} for s in self._targets(self.issues[issueIdOrKey])]}

    def tool_transitionJiraIssue(self, issueIdOrKey, transitionId):
        i = self.issues[issueIdOrKey]
        target = transitionId.removeprefix("t-")
        if target not in self._targets(i):
            return {"error": "TRANSITION_FAILED", "status": 400, "message": "Transition is not valid", "allowedValues": []}
        i["status"], i["updated"] = target, self._tick()
        return {"statusName": target}

    def tool_listJiraIssueComments(self, issueIdOrKey, startAt, maxResults, orderBy):
        found = self.comments[issueIdOrKey]
        return {"comments": found[startAt:startAt + min(maxResults, self.page)], "total": len(found), "startAt": startAt}

    def tool_addOrEditJiraIssueComment(self, issueIdOrKey, commentBody):
        row = {"id": str(self._next()), "author": {"displayName": "Bot"}, "body": commentBody, "created": self._tick()}
        self.comments[issueIdOrKey].append(row)
        return {"message": "Comment added", "commentId": row["id"], "body": commentBody}

    def _next(self):
        self.seq += 1
        return self.seq

    def tool_createJiraIssueLink(self, linkType, inwardIssue, outwardIssue):
        self.links.append((linkType, inwardIssue, outwardIssue))
        return {"ok": True}

    def tool_listJiraProjectIssueTypesMetadata(self, projectIdOrKey, maxResults):
        return {"issueTypes": TYPES}

    def tool_getJiraIssueTypeMetaWithFields(self, projectIdOrKey, issueTypeId, maxResults):
        base = [{"fieldId": "summary", "name": "Summary", "required": True},
                {"fieldId": "issuetype", "name": "Type", "required": True}]
        return {"fields": base + [{"fieldId": f, "name": f, "required": True} for f in self.required]}

    def tool_executeRead(self, **_):  # visible in tools/list; calls are unwrapped above
        raise AssertionError

    tool_executeWrite = tool_executeRead


@pytest.fixture
def fake():
    return FakeJira()


@pytest.fixture
def backend(fake):
    return JiraWorkBackend(fake, SETTINGS)


def spec(title="Spec", state=Status.READY, parent=None, deps=()):
    return WorkItem("", "spec", title, SPEC, state, parent_id=parent, dependencies=list(deps))


def epic(title="Epic"):
    return WorkItem("", "epic", title, "Epic body", Status.DRAFT)


def test_satisfies_protocol_and_requires_settings(fake, backend):
    assert isinstance(backend, WorkBackend)
    with pytest.raises(WorkConfigError, match="project_key"):
        JiraWorkBackend(fake, {"cloud_id": "c"})


def test_epic_and_child_spec_are_related_natively(fake, backend):
    parent = backend.create(epic())
    child = backend.create(spec(parent=parent.id))
    assert parent.kind == "epic" and fake.issues[parent.id]["type"] == "Epic"
    assert child.kind == "spec" and fake.issues[child.id]["type"] == "Task"
    assert child.parent_id == parent.id and child.description == SPEC
    assert [i.id for i in backend.list_specs(parent.id, Status.READY)] == [child.id]


def test_configured_spec_type_is_used(fake):
    backend = JiraWorkBackend(fake, {**SETTINGS, "issue_type_spec": "Story"})
    assert fake.issues[backend.create(spec()).id]["type"] == "Story"


def test_unknown_or_unsuitable_issue_type_fails_before_writing(fake):
    with pytest.raises(CapabilityMissing, match="Epic, Task, Story"):
        JiraWorkBackend(fake, {**SETTINGS, "issue_type_spec": "Nope"}).create(spec())
    with pytest.raises(CapabilityMissing, match="issue_type_spec"):
        JiraWorkBackend(fake, {**SETTINGS, "issue_type_spec": "Sub-task"}).create(spec())
    assert not fake.issues


def test_required_custom_field_is_reported_before_writing():
    fake = FakeJira(required=["customfield_1"])
    with pytest.raises(CapabilityMissing, match="customfield_1"):
        JiraWorkBackend(fake, SETTINGS).create(spec())
    assert not fake.issues


def test_draft_and_ready_share_a_status_through_the_label(fake, backend):
    item = backend.create(spec(state=Status.DRAFT))
    assert item.status == Status.DRAFT and READY_LABEL not in fake.issues[item.id]["labels"]
    assert backend.transition(item.id, Status.READY).status == Status.READY
    assert READY_LABEL in fake.issues[item.id]["labels"] and fake.issues[item.id]["status"] == "To Do"
    assert not fake.used("transitionJiraIssue")
    assert backend.transition(item.id, Status.DRAFT).status == Status.DRAFT


def test_full_lifecycle_uses_offered_transitions(fake, backend):
    item = backend.create(spec())
    assert backend.transition(item.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    assert READY_LABEL not in fake.issues[item.id]["labels"]
    assert backend.transition(item.id, Status.DONE).status == Status.DONE
    assert fake.issues[item.id]["status"] == "Done"
    assert all(a["transitionId"].startswith("t-") for a in fake.used("transitionJiraIssue"))


def test_native_blocked_status_is_used_when_reachable(fake, backend):
    item = backend.create(spec())
    blocked = backend.transition(item.id, Status.BLOCKED, blocker="Need keys")
    assert blocked.status == Status.BLOCKED and blocked.blocker == "Need keys"
    assert fake.issues[item.id]["status"] == "Blocked" and BLOCKED_LABEL not in fake.issues[item.id]["labels"]
    assert backend.get(item.id).blocker == "Need keys"
    assert backend.transition(item.id, Status.READY).status == Status.READY
    assert fake.issues[item.id]["status"] == "To Do"


def test_without_a_blocked_status_the_label_keeps_the_current_status():
    fake = FakeJira(statuses=NO_BLOCKED)
    backend = JiraWorkBackend(fake, LABEL_BLOCKED)
    item = backend.create(spec())
    backend.transition(item.id, Status.IN_PROGRESS)
    assert backend.transition(item.id, Status.BLOCKED).status == Status.BLOCKED
    assert fake.issues[item.id]["status"] == "In Progress" and BLOCKED_LABEL in fake.issues[item.id]["labels"]
    assert backend.transition(item.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    assert BLOCKED_LABEL not in fake.issues[item.id]["labels"]


def test_backlog_draft_needs_no_labels():
    fake = FakeJira(statuses=["Backlog", "To Do", "In Progress", "Done"])
    backend = JiraWorkBackend(fake, {**LABEL_BLOCKED, "status_draft": "Backlog"})
    draft = backend.create(spec(state=Status.DRAFT))
    assert draft.status == Status.DRAFT and fake.issues[draft.id]["status"] == "Backlog"
    assert backend.transition(draft.id, Status.READY).status == Status.READY
    assert fake.issues[draft.id]["status"] == "To Do" and READY_LABEL not in fake.issues[draft.id]["labels"]


def test_new_issue_is_not_executable_until_it_is_finished():
    fake = FakeJira()
    backend = JiraWorkBackend(fake, SETTINGS)
    a = backend.create(spec("A"))
    fake.lose_response_of.add("createJiraIssueLink")
    with pytest.raises(TransportFailure):
        backend.create(spec("B", deps=[a.id]), "k")
    pending = next(i for i in fake.issues.values() if i["summary"] == "B")
    assert PENDING_LABEL in pending["labels"] and backend.get(pending["key"]).status == Status.DRAFT
    fake.links.clear()
    done = backend.create(spec("B", deps=[a.id]), "k")
    assert done.status == Status.READY and PENDING_LABEL not in fake.issues[done.id]["labels"]


def test_mapping_is_required_and_consistent(fake):
    with pytest.raises(WorkConfigError, match="backend connect"):
        JiraWorkBackend(fake, {"cloud_id": "c", "project_key": "K"})
    with pytest.raises(WorkConfigError, match="same Jira status"):
        JiraWorkBackend(fake, {**SETTINGS, "status_done": "In Progress"})


def test_mapping_overrides_misleading_categories():
    fake = FakeJira()
    backend = JiraWorkBackend(fake, SETTINGS)
    item = backend.create(spec())
    S["Done"], S["Blocked"] = "indeterminate", "done"
    try:
        assert backend.transition(item.id, Status.DONE).status == Status.DONE
        assert backend.transition(item.id, Status.BLOCKED).status == Status.BLOCKED
    finally:
        S["Done"], S["Blocked"] = "done", "indeterminate"


def test_proposal_prefers_names_and_backlog_for_draft():
    swapped = [{"name": "Done", "category": "indeterminate"}, {"name": "Blocked", "category": "done"},
               {"name": "In Progress", "category": "indeterminate"}, {"name": "To Do", "category": "new"}]
    assert propose_mapping(swapped) == {"status_draft": "To Do", "status_ready": "To Do", "status_in_progress": "In Progress",
                                        "status_done": "Done", "status_blocked": "Blocked"}
    proposal = propose_mapping([{"name": "Backlog", "category": "new"}, *swapped])
    assert proposal["status_draft"] == "Backlog" and proposal["status_ready"] == "To Do"


def test_check_mapping_is_per_issue_type():
    data = {"data": {"statuses": [{"id": "1", "name": "To Do"}, {"id": "2", "name": "In Progress"}, {"id": "3", "name": "Done"},
                                  {"id": "4", "name": "Blocked"}],
                     "workTypes": [{"name": "Task", "statusIds": ["1", "2", "3", "4"]}, {"name": "Epic", "statusIds": ["1", "2", "3"]}]}}
    check_mapping(StatusPolicy(SETTINGS), data, ["Task"])
    with pytest.raises(WorkConfigError, match="Epic has no status 'Blocked'"):
        check_mapping(StatusPolicy(SETTINGS), data, ["Task", "Epic"])


def test_unavailable_transition_fails_clearly_and_changes_nothing():
    fake = FakeJira(routes={"To Do": ["In Progress"]})
    backend = JiraWorkBackend(fake, SETTINGS)
    item = backend.create(spec())
    with pytest.raises(InvalidTransition, match="available: In Progress"):
        backend.transition(item.id, Status.DONE)
    assert fake.issues[item.id]["status"] == "To Do" and READY_LABEL in fake.issues[item.id]["labels"]


def test_failed_transition_reported_only_in_payload_is_detected(fake, backend):
    item = backend.create(spec())
    fake.tool_listJiraIssueTransitions = lambda issueIdOrKey: {"transitions": [{"id": "t-Done", "to": status("Done")}]}
    fake.routes = {"To Do": []}
    with pytest.raises(InvalidTransition, match="Transition is not valid"):
        backend.transition(item.id, Status.DONE)
    assert READY_LABEL in fake.issues[item.id]["labels"], "labels must not change after a failed move"


def test_status_outside_the_mapping_is_neither_runnable_nor_done(fake, backend):
    service = WorkService(backend)
    a = backend.create(spec("A"))
    b = backend.create(spec("B", deps=[a.id]))
    fake.issues[a.id]["status"] = "Rejected"
    assert backend.get(a.id).status == "Rejected"
    assert service.eligible_specs(b.id) == []


def test_unknown_status_is_rejected(backend):
    with pytest.raises(InvalidTransition):
        backend.create(spec(state="NOPE"))


def test_status_policy_describes_the_strategy():
    text = StatusPolicy(LABEL_BLOCKED).describe()
    assert READY_LABEL in text and BLOCKED_LABEL in text


def test_comment_id_comes_from_the_create_response(fake, backend):
    item = backend.create(spec())
    assert backend.add_comment(item.id, "x").id == fake.comments[item.id][0]["id"]


def test_retry_does_not_duplicate_and_keeps_status(fake, backend):
    first = backend.create(spec(), "key-1")
    backend.transition(first.id, Status.IN_PROGRESS)
    again = backend.create(spec(), "key-1")
    assert again.id == first.id and again.metadata["reused"]
    assert again.status == Status.IN_PROGRESS and len(fake.issues) == 1


def test_same_title_and_parent_counts_as_the_same_work(fake, backend):
    assert backend.create(spec()).id == backend.create(spec(" SPEC ")).id and len(fake.issues) == 1


def test_lost_create_response_recovers_the_issue(fake, backend):
    fake.lose_response_of.add("createJiraIssue")
    item = backend.create(spec(), "k")
    assert item.metadata["reused"] and len(fake.issues) == 1


def test_operation_key_stays_out_of_the_description(fake, backend):
    item = backend.create(spec(), "k")
    assert item.description == SPEC and item.metadata["op_key"]
    assert "ae-sdlc-op" not in fake.issues[item.id]["description"]


def test_dependencies_round_trip_with_correct_direction(fake, backend):
    a = backend.create(spec("A"))
    b = backend.create(spec("B", deps=[a.id]))
    assert fake.links == [("Blocks", a.id, b.id)]
    assert backend.get(b.id).dependencies == [a.id] and backend.get(a.id).dependencies == []
    assert [i.id for i in backend.get_dependencies(b.id)] == [a.id]


def test_interrupted_create_is_completed_by_its_retry(fake, backend):
    a = backend.create(spec("A"))
    fake.lose_response_of.add("createJiraIssueLink")
    with pytest.raises(TransportFailure):
        backend.create(spec("B", deps=[a.id]), "k")
    fake.links.clear()
    assert backend.create(spec("B", deps=[a.id]), "k").dependencies == [a.id]


def test_related_issues_are_separate_from_blockers(fake, backend):
    a, b = backend.create(spec("A")), backend.create(spec("B"))
    fake.links.append(("Relates", a.id, b.id))
    assert backend.get(a.id).related_ids == [b.id] and backend.get(a.id).dependencies == []
    assert [i.id for i in backend.get_related(b.id)] == [a.id]


def test_missing_links_response_is_not_read_as_no_dependencies(fake, backend):
    item = backend.create(spec())
    original = fake.tool_getJiraIssue
    def stripped(issueIdOrKey, view):
        data = original(issueIdOrKey, view)
        del data["fields"]["issuelinks"]
        return data
    fake.tool_getJiraIssue = stripped
    with pytest.raises(Exception, match="issue links"):
        backend.get(item.id)


def test_update_replaces_content_and_adds_links(fake, backend):
    a, b = backend.create(spec("A")), backend.create(spec("B"))
    b.title, b.description, b.dependencies = "B2", "New", [a.id]
    updated = backend.update(b, expected_revision=b.revision)
    assert (updated.title, updated.description, updated.dependencies) == ("B2", "New", [a.id])
    assert updated.status == Status.READY, "update must not touch status labels"


def test_update_rejects_a_stale_revision(fake, backend):
    item = backend.create(spec())
    with pytest.raises(RevisionConflict):
        backend.update(item, expected_revision="old")


def test_removing_a_link_is_refused_without_a_partial_write(fake, backend):
    a = backend.create(spec("A"))
    b = backend.create(spec("B", deps=[a.id]))
    writes = len(fake.used("editJiraIssue"))
    b.dependencies, b.title = [], "Changed"
    with pytest.raises(CapabilityMissing, match="cannot delete"):
        backend.update(b)
    assert len(fake.used("editJiraIssue")) == writes and fake.issues[b.id]["summary"] == "B"


def test_update_refuses_content_markdown_would_lose(fake, backend):
    item = backend.create(spec())
    original = fake.tool_getJiraIssue
    fake.tool_getJiraIssue = lambda issueIdOrKey, view: {**original(issueIdOrKey, view), "appliedContentFormat": "html"}
    with pytest.raises(CapabilityMissing, match="Markdown"):
        backend.update(item)


def test_comments_paginate_and_honor_limit(fake, backend):
    item = backend.create(spec())
    for n in range(5):
        backend.add_comment(item.id, f"note {n}")
    assert [c.body for c in backend.get_comments(item.id)] == [f"note {n}" for n in range(5)]
    assert [c.body for c in backend.get_comments(item.id, limit=2)] == ["note 3", "note 4"]
    assert backend.get_comments(item.id)[0].author == "Bot"


def test_comment_dedupe_survives_lost_response_and_hides_marker(fake, backend):
    item = backend.create(spec())
    fake.lose_response_of.add("addOrEditJiraIssueComment")
    first = backend.add_comment(item.id, "Once", dedupe_key="d")
    assert backend.add_comment(item.id, "Once", dedupe_key="d").id == first.id
    assert len(fake.comments[item.id]) == 1 and first.body == "Once"


def test_listing_pages_and_only_returns_managed_work(fake, backend):
    for n in range(5):
        backend.create(spec(f"S{n}"))
    fake.tool_createJiraIssue("KAN", "Foreign", "Task", "x", [])
    assert len(backend.list_items()) == 5


def test_search_escapes_query_and_finds_unmanaged_work(fake, backend):
    fake.tool_createJiraIssue("KAN", 'Say "hi"', "Task", "x", [])
    backend.search('Say "hi"')
    assert 'Say \\"hi\\"' in fake.used("searchJiraIssuesUsingJql")[-1]["jql"]


def test_unknown_item_is_not_found_and_unknown_scope_is_empty(backend):
    with pytest.raises(WorkItemNotFound):
        backend.get("KAN-999")
    assert backend.list_specs("KAN-999") == []


def test_permission_failures_surface_as_actionable_errors(fake, backend):
    item = backend.create(spec())
    fake.denied.add("addOrEditJiraIssueComment")
    with pytest.raises(PermissionDenied, match="lacks permission"):
        backend.add_comment(item.id, "x")


def test_missing_link_capability_fails_before_writing(fake, backend):
    fake.hide.add("executeWrite")
    with pytest.raises(CapabilityMissing, match="createJiraIssueLink"):
        backend.create(spec(deps=["KAN-1"]))
    assert not fake.issues


def test_service_runs_only_specs_whose_dependencies_are_done(fake, backend):
    service = WorkService(backend)
    parent = backend.create(epic())
    a = backend.create(spec("A", parent=parent.id))
    b = backend.create(spec("B", parent=parent.id, deps=[a.id]))
    assert [i.id for i in service.eligible_specs(parent.id)] == [a.id]
    service.start_execution(a.id)
    assert fake.issues[a.id]["status"] == "In Progress"
    service.finish_execution(a.id, 0, {"status": "COMPLETED"}, "fallback")
    assert fake.issues[a.id]["status"] == "Done"
    assert [i.id for i in service.eligible_specs(parent.id)] == [b.id]


def _ignore_transitions(fake, monkeypatch, times):
    """Jira answers success and leaves the status alone, as seen against a real site."""
    real, left = fake.tool_transitionJiraIssue, [times]

    def flaky(issueIdOrKey, transitionId):
        if left[0]:
            left[0] -= 1
            return {"statusName": transitionId.removeprefix("t-")}
        return real(issueIdOrKey, transitionId)

    monkeypatch.setattr(fake, "tool_transitionJiraIssue", flaky)
    monkeypatch.setattr("ae_sdlc.work.jira._CONFIRM_DELAY_SECONDS", 0)


def test_transition_repeats_when_jira_reports_success_without_moving(fake, backend, monkeypatch):
    item = backend.create(spec())
    _ignore_transitions(fake, monkeypatch, 2)
    assert backend.transition(item.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    assert fake.issues[item.id]["status"] == "In Progress"


def test_transition_fails_when_jira_never_applies_it(fake, backend, monkeypatch):
    item = backend.create(spec())
    _ignore_transitions(fake, monkeypatch, 3)
    with pytest.raises(InvalidTransition, match="after requesting"):
        backend.transition(item.id, Status.IN_PROGRESS)
