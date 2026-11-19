"""PR 3: LinearWorkBackend against a stateful fake that follows the recorded Linear tool schemas."""
from __future__ import annotations

import copy
import inspect
from types import SimpleNamespace

import pytest

from ae_sdlc.work.backend import (CapabilityMissing, InvalidTransition, RevisionConflict, WorkBackend, WorkConfigError,
                                  WorkItemNotFound)
from ae_sdlc.work.linear import LinearWorkBackend
from ae_sdlc.work.linear_status import BLOCKED_LABEL, CANCELED, build_status_mapping
from ae_sdlc.work.mcp.client import ToolResponse
from ae_sdlc.work.mcp.errors import ToolFailed, TransportFailure
from ae_sdlc.work.models import Status, WorkItem
from ae_sdlc.work.service import WorkService

STATES = [{"name": "Backlog", "type": "backlog"}, {"name": "Todo", "type": "unstarted"},
          {"name": "In Progress", "type": "started"}, {"name": "In Review", "type": "started"},
          {"name": "Done", "type": "completed"}, {"name": "Canceled", "type": "canceled"},
          {"name": "Duplicate", "type": "canceled"}]
PROJECT_UUID = "00000000-0000-0000-0000-000000000001"
SPEC = """## Goal and Outcome

Ship it.

| a | b |
|---|---|
| 1 | 2 |

```python
print("Привіт")
```

- [ ] Acceptance
"""


class FakeLinear:
    """In-process Linear: enough state to observe what a real workspace would end up holding."""

    def __init__(self, tools=None, states=STATES, page_size=2):
        self.states, self.page_size = states, page_size
        self.issues, self.projects, self.comments, self.labels = {}, {}, {}, set()
        self.calls, self.lose_response_of = [], set()
        self.clock = 0
        self.tool_names = tools
        self.hidden_arguments: set[str] = set()  # arguments the server's published schema does not list

    def list_tools(self):
        names = self.tool_names or [n[5:] for n in dir(self) if n.startswith("tool_") and n != "tool_names"]
        return [SimpleNamespace(name=n, input_schema=self._schema(n)) for n in names]

    def _schema(self, tool):
        """Like the real server, publish every argument the tool accepts."""
        parameters = inspect.signature(getattr(self, f"tool_{tool}")).parameters
        return {"properties": {p: {} for p in parameters if p not in self.hidden_arguments}}

    def call(self, tool, arguments, *, retry_safe=False):
        self.calls.append((tool, copy.deepcopy(arguments)))
        data = getattr(self, f"tool_{tool}")(**arguments)
        if tool in self.lose_response_of:
            self.lose_response_of.discard(tool)
            raise TransportFailure("connection dropped after the server applied the request")
        return ToolResponse("", data)

    def writes(self, tool):
        return [args for name, args in self.calls if name == tool]

    def _tick(self):
        self.clock += 1
        return f"2026-11-09T00:00:{self.clock:02d}Z"

    def _page(self, found, key, cursor=None):
        start = int(cursor or 0)
        end = start + self.page_size
        more = end < len(found)
        return {key: found[start:end], "hasNextPage": more, **({"cursor": str(end)} if more else {})}

    def _state(self, name):
        for state in self.states:
            if name.lower() in (state["name"].lower(), state["type"]):
                return state
        raise ToolFailed(f'Could not find state "{name}"')

    def _project(self, ref):
        for p in self.projects.values():
            if ref in (p["id"], p["uuid"], p["name"]):
                return p
        return None

    def _row(self, issue, fields):
        row = {k: copy.deepcopy(v) for k, v in issue.items() if k in fields}
        if "relations" in fields:
            row["relations"] = {
                "blockedBy": [{"id": b} for b in issue["blocked_by"]],
                "blocks": [{"id": i["id"]} for i in self.issues.values() if issue["id"] in i["blocked_by"]],
                "relatedTo": [{"id": r} for r in issue["related"]],
            }
        return row

    def tool_list_issue_statuses(self, team):
        return self.states

    def tool_get_team(self, query):
        return {"id": "team-uuid", "name": query}

    def tool_list_issue_labels(self, name=None, team=None):
        return {"labels": [{"name": n} for n in self.labels if n == name]}

    def tool_save_issue_label(self, name, teamId=None):
        self.labels.add(name)
        return {"id": name, "name": name}

    def _issue(self, ref):
        return next((i for i in self.issues.values() if ref in (i["id"], i["uuid"])), None)

    def tool_get_issue(self, id, fields):
        issue = self._issue(id)
        if issue is None:
            raise ToolFailed("Entity not found: Issue")
        assert "relations" in fields
        return self._row(issue, fields)

    def tool_list_issues(self, team=None, label=None, project=None, parentId=None, query=None, limit=50, fields=(),
                         cursor=None):
        assert "relations" not in fields, "the real list_issues schema has no relations field"
        if project is not None and self._project(project) is None:
            raise ToolFailed("Entity not found: Project")
        parent = self._issue(parentId) if parentId is not None else None
        found = [i for i in self.issues.values()
                 if (label is None or label in i["labels"])
                 and (project is None or i["projectId"] == self._project(project)["uuid"])
                 and (parentId is None or (parent is not None and i["parentId"] == parent["uuid"]))
                 and (query is None or query.lower() in (i["title"] + i["description"]).lower())]
        return self._page([self._row(i, fields) for i in found], "issues", cursor)

    def tool_save_issue(self, id=None, team=None, title=None, description=None, state=None, labels=None, project=...,
                        addLabels=(), removeLabels=(), blockedBy=(), removeBlockedBy=(), parentId=..., assignee=None):
        for label in [*(labels or ()), *addLabels]:
            if label not in self.labels:
                raise ToolFailed(f"Label not found: {label}")
        if id is None:
            assert team and title
            number = len(self.issues) + 5
            id = f"AND-{number}"
            self.issues[id] = {"id": id, "uuid": f"00000000-0000-0000-0001-{number:012d}", "title": title,
                               "description": "", "url": f"https://l/{id}", "status": "Backlog",
                               "statusType": "backlog", "labels": [], "project": None, "projectId": None, "parentId": None,
                               "blocked_by": [], "related": []}
        elif id not in self.issues:
            raise ToolFailed("Entity not found: Issue")
        issue = self.issues[id]
        if state is not None:
            found = self._state(state)
            issue["status"], issue["statusType"] = found["name"], found["type"]
        if assignee is not None:
            issue["assignee"] = assignee
        if title is not None:
            issue["title"] = title
        if description is not None:
            issue["description"] = description
        if labels is not None:
            issue["labels"] = list(labels)
        issue["labels"] = [x for x in [*issue["labels"], *addLabels] if x not in removeLabels]
        if project is not ...:
            target = self._project(project) if project else None
            issue["projectId"], issue["project"] = (target["uuid"], target["name"]) if target else (None, None)
        if parentId is not ...:
            parent = self._issue(parentId) if parentId else None
            if parentId and parent is None:
                raise ToolFailed("Entity not found: Issue")
            issue["parentId"] = parent["uuid"] if parent else None  # Linear answers with the parent's UUID
        issue["blocked_by"] = [b for b in [*issue["blocked_by"], *blockedBy] if b not in removeBlockedBy]
        issue["updatedAt"] = self._tick()
        return {"id": id}

    def tool_get_project(self, query):
        project = self._project(query)
        if project is None:
            raise ToolFailed("Entity not found: Project")
        return copy.deepcopy(project)

    def tool_list_projects(self, team=None, query=None, limit=50, fields=(), cursor=None):
        assert limit <= 50
        found = [{k: v for k, v in p.items() if k in fields} for p in self.projects.values()
                 if query is None or query.lower() in p["name"].lower()]
        return self._page(found, "projects", cursor)

    def tool_save_project(self, id=None, name=None, description=None, addTeams=(), state=None):
        if id is None:
            assert name and addTeams
            number = len(self.projects) + 1
            id = f"P-AND-{number}"
            self.projects[id] = {"id": id, "uuid": f"00000000-0000-0000-0000-{number:012d}", "name": name, "description": "",
                                 "url": f"https://l/{id}", "status": {"name": "Backlog", "type": "backlog"}}
        project = self._project(id)
        if name is not None:
            project["name"] = name
        if description is not None:
            project["description"] = description
        if state is not None:
            project["status"] = {"name": state.title(), "type": state}
        project["updatedAt"] = self._tick()
        return {"id": project["id"]}

    def tool_list_comments(self, issueId=None, projectId=None, limit=50, orderBy="updatedAt", cursor=None):
        found = list(reversed(self.comments.get(issueId or projectId, [])))  # the fake is newest-first
        return self._page(found, "comments", cursor)

    def tool_save_comment(self, body, issueId=None, projectId=None):
        parent = issueId or projectId
        row = {"id": f"c{sum(map(len, self.comments.values()))}", "body": body, "author": {"name": "Bot"},
               "createdAt": self._tick()}
        self.comments.setdefault(parent, []).append(row)
        return row


@pytest.fixture
def fake():
    return FakeLinear()


@pytest.fixture
def backend(fake):
    return LinearWorkBackend(fake, {"team": "AND"})


def spec(title="Spec", status=Status.READY, parent=None, deps=()):
    return WorkItem(id="", kind="spec", title=title, description=SPEC, status=status, parent_id=parent,
                    dependencies=list(deps))


def epic(title="Epic"):
    return WorkItem(id="", kind="epic", title=title, description="Epic body", status=Status.DRAFT)


def test_satisfies_protocol_and_requires_a_team(fake, backend):
    assert isinstance(backend, WorkBackend)
    with pytest.raises(WorkConfigError):
        LinearWorkBackend(fake, {})


# --- prepare ---

def test_prepare_persists_project_and_child_issue_with_full_spec(fake, backend):
    project = backend.create(epic())
    issue = backend.create(spec(parent=project.id))
    assert (project.id, project.kind, project.status) == ("P-AND-1", "epic", Status.DRAFT)
    assert fake.issues[issue.id]["projectId"] == PROJECT_UUID
    assert fake.issues[issue.id]["status"] == "Todo"
    assert issue.status == Status.READY and issue.url and issue.revision
    assert issue.description == SPEC.rstrip() or issue.description == SPEC
    assert SPEC.rstrip() in fake.issues[issue.id]["description"]
    assert backend.get(project.id).title == "Epic"


def test_standalone_issue_has_no_project(fake, backend):
    issue = backend.create(spec())
    assert issue.parent_id is None and fake.issues[issue.id]["projectId"] is None


def test_marker_label_is_created_once_at_team_level(fake, backend):
    backend.create(spec("A"))
    backend.create(spec("B"))
    assert fake.writes("save_issue_label") == [{"name": "ae-sdlc", "teamId": "team-uuid"}]
    assert all("ae-sdlc" in i["labels"] for i in fake.issues.values())


def test_existing_label_is_not_recreated(fake, backend):
    fake.labels.add("ae-sdlc")
    backend.create(spec())
    assert fake.writes("save_issue_label") == []


# --- duplicate prevention ---

def test_repeat_create_returns_the_existing_issue(fake, backend):
    first = backend.create(spec())
    again = backend.create(spec())
    assert again.id == first.id and again.metadata["reused"] is True
    assert len(fake.issues) == 1


def test_same_title_under_another_parent_is_different_work(fake, backend):
    one, two = backend.create(epic("E1")), backend.create(epic("E2"))
    backend.create(spec(parent=one.id))
    backend.create(spec(parent=two.id))
    assert len(fake.issues) == 2


def test_idempotency_key_survives_a_title_change(fake, backend):
    first = backend.create(spec("Old"), idempotency_key="prepare-1")
    assert backend.create(spec("New"), idempotency_key="prepare-1").id == first.id
    assert len(fake.issues) == 1


def test_lost_create_response_recovers_without_a_duplicate(fake, backend):
    fake.lose_response_of.add("save_issue")
    recovered = backend.create(spec())
    assert recovered.metadata["reused"] is True and len(fake.issues) == 1


def test_lost_epic_create_response_recovers_without_a_duplicate(fake, backend):
    fake.lose_response_of.add("save_project")
    assert backend.create(epic()).metadata["reused"] is True
    assert len(fake.projects) == 1


def test_create_that_never_landed_reports_the_failure(fake, backend):
    def refuse(**_):
        raise TransportFailure("cannot reach mcp.linear.app")
    fake.tool_save_issue = refuse
    with pytest.raises(TransportFailure):
        backend.create(spec())


def test_canceled_issue_does_not_block_recreation(fake, backend):
    first = backend.create(spec())
    fake.tool_save_issue(id=first.id, state="Canceled")
    assert backend.create(spec()).id != first.id


# --- reads ---

def test_get_by_key_loads_ready_work(backend):
    created = backend.create(spec())
    loaded = backend.get(created.id)
    assert loaded.status == Status.READY and loaded.description.rstrip() == SPEC.rstrip()


@pytest.mark.parametrize("ref", ["AND-99", "P-AND-99", "not-a-uuid"])
def test_unknown_references_are_not_found(backend, ref):
    with pytest.raises(WorkItemNotFound):
        backend.get(ref)


def test_project_resolves_by_uuid(backend):
    created = backend.create(epic())
    assert backend.get(PROJECT_UUID).id == created.id


def test_unmanaged_issue_is_loadable_by_key_but_not_listed(fake, backend):
    fake.labels.add("ae-sdlc")
    fake.tool_save_issue(team="AND", title="Team bug", description="x", state="Todo")
    assert backend.get("AND-5").metadata["managed"] is False
    assert backend.list_items() == []


def test_list_items_before_any_managed_work_writes_nothing(fake, backend):
    assert backend.list_items() == []
    assert fake.writes("save_issue_label") == []


def test_list_specs_filters_project_pages_and_excludes_unmanaged(fake, backend):
    project = backend.create(epic())
    created = [backend.create(spec(f"S{i}", parent=project.id)) for i in range(5)]
    fake.tool_save_issue(team="AND", title="Unmanaged", description="", state="Todo", project=project.id)
    backend.transition(created[0].id, Status.DONE)
    ready = backend.list_specs(project.id, status=Status.READY)
    assert [i.id for i in ready] == [c.id for c in created[1:]]
    assert len(backend.list_specs(project.id)) == 5


def test_list_specs_sorts_numerically_and_handles_unknown_scope(fake, backend):
    project = backend.create(epic())
    for i in range(7):
        backend.create(spec(f"S{i}", parent=project.id))
    ids = [i.id for i in backend.list_specs(project.id)]
    assert ids == [f"AND-{n}" for n in range(5, 12)]
    assert backend.list_specs("AND-404") == []


def test_list_specs_on_a_single_issue_honours_status(backend):
    issue = backend.create(spec())
    assert [i.id for i in backend.list_specs(issue.id, Status.READY)] == [issue.id]
    assert backend.list_specs(issue.id, Status.DONE) == []


def test_search_finds_unmanaged_issues_and_projects(fake, backend):
    backend.create(epic("Payments"))
    fake.tool_save_issue(team="AND", title="Payments bug", description="", state="Todo")
    assert {i.kind for i in backend.search("payments")} == {"epic", "spec"}


def test_related_issues_are_returned(fake, backend):
    a, b = backend.create(spec("A")), backend.create(spec("B"))
    fake.issues[a.id]["related"] = [b.id, "AND-404"]
    assert [i.id for i in backend.get_related(a.id)] == [b.id]


def test_response_without_description_is_not_treated_as_empty(fake, backend):
    issue = backend.create(spec())
    del fake.issues[issue.id]["description"]
    with pytest.raises(Exception, match="without an identifier or description"):
        backend.get(issue.id)


# --- dependencies ---

def test_dependencies_round_trip_and_gate_eligibility(fake, backend):
    project = backend.create(epic())
    first = backend.create(spec("First", parent=project.id))
    second = backend.create(spec("Second", parent=project.id, deps=[first.id]))
    assert backend.get(second.id).dependencies == [first.id]
    assert [d.id for d in backend.get_dependencies(second.id)] == [first.id]
    service = WorkService(backend)
    assert [s.id for s in service.eligible_specs(project.id)] == [first.id]
    service.start_execution(first.id)
    service.finish_execution(first.id, 0, {"status": "COMPLETED", "summary": "ok"}, "failed")
    assert [s.id for s in service.eligible_specs(project.id)] == [second.id]


def test_canceled_dependency_never_satisfies(fake, backend):
    first = backend.create(spec("First"))
    second = backend.create(spec("Second", deps=[first.id]))
    fake.tool_save_issue(id=first.id, state="Canceled")
    assert backend.get(first.id).status == CANCELED
    assert WorkService(backend).eligible_specs(second.id) == []


def test_update_applies_a_dependency_diff(fake, backend):
    a, b, c = (backend.create(spec(t)) for t in "ABC")
    backend.update(WorkItem(c.id, "spec", "C", SPEC, c.status, dependencies=[a.id]))
    backend.update(WorkItem(c.id, "spec", "C", SPEC, c.status, dependencies=[b.id]))
    assert backend.get(c.id).dependencies == [b.id]
    last = fake.writes("save_issue")[-1]
    assert last["blockedBy"] == [b.id] and last["removeBlockedBy"] == [a.id]


def test_missing_dependency_capability_is_an_error(fake):
    names = [n[5:] for n in dir(FakeLinear) if n.startswith("tool_") and n != "tool_save_issue"]
    fake.tool_names = names
    backend = LinearWorkBackend(fake, {"team": "AND"})
    with pytest.raises(CapabilityMissing, match="save_issue"):
        backend.create(spec(deps=["AND-1"]))


def test_missing_project_creation_is_an_error(fake):
    fake.tool_names = [n[5:] for n in dir(FakeLinear) if n.startswith("tool_") and n != "tool_save_project"]
    with pytest.raises(CapabilityMissing, match="save_project"):
        LinearWorkBackend(fake, {"team": "AND"}).create(epic())


# --- update ---

def test_update_keeps_the_operation_key_and_checks_revision(fake, backend):
    issue = backend.create(spec())
    updated = backend.update(WorkItem(issue.id, "spec", "Renamed", "New body", issue.status), issue.revision)
    assert (updated.title, updated.description.strip()) == ("Renamed", "New body")
    assert "ae-sdlc-op:" in fake.issues[issue.id]["description"]
    with pytest.raises(RevisionConflict):
        backend.update(WorkItem(issue.id, "spec", "Late", "Late", issue.status), issue.revision)
    assert fake.issues[issue.id]["title"] == "Renamed"


def test_update_moves_between_projects_and_detaches(fake, backend):
    one, issue = backend.create(epic()), backend.create(spec())
    backend.update(WorkItem(issue.id, "spec", issue.title, SPEC, issue.status, parent_id=one.id))
    assert fake.issues[issue.id]["projectId"] == PROJECT_UUID
    backend.update(WorkItem(issue.id, "spec", issue.title, SPEC, issue.status))
    assert fake.issues[issue.id]["projectId"] is None


def test_update_project_changes_name_and_description(fake, backend):
    project = backend.create(epic())
    updated = backend.update(WorkItem(project.id, "epic", "Renamed", "New", project.status))
    assert (updated.title, updated.description.strip()) == ("Renamed", "New")
    assert updated.metadata["managed"] is True


# --- status ---

def test_lifecycle_persists_remotely(fake, backend):
    issue = backend.create(spec())
    assert WorkService(backend).start_execution(issue.id).status == Status.IN_PROGRESS
    assert fake.issues[issue.id]["status"] == "In Progress"
    assert backend.transition(issue.id, Status.DONE).status == Status.DONE
    assert fake.issues[issue.id]["status"] == "Done"


def test_in_review_reads_as_in_progress(fake, backend):
    issue = backend.create(spec())
    fake.tool_save_issue(id=issue.id, state="In Review")
    assert backend.get(issue.id).status == Status.IN_PROGRESS


# --- review step: only when [linear] status_review names a state ---

@pytest.fixture
def reviewing(fake):
    return LinearWorkBackend(fake, {"team": "AND", "status_review": "In Review"})


def test_there_is_no_review_step_unless_a_state_is_pinned(fake, backend):
    assert backend.supports_review is False
    assert WorkService(backend).completion_status == Status.DONE


def test_pinned_review_state_maps_both_ways(fake, reviewing):
    issue = reviewing.create(spec())
    assert reviewing.supports_review is True
    assert reviewing.transition(issue.id, Status.REVIEW).status == Status.REVIEW
    assert fake.issues[issue.id]["status"] == "In Review"
    assert reviewing.get(issue.id).status == Status.REVIEW
    assert reviewing.transition(issue.id, Status.DONE).status == Status.DONE


def test_completed_work_waits_in_review_and_unblocks_dependents(fake, reviewing):
    project = reviewing.create(epic())
    first = reviewing.create(spec("First", parent=project.id))
    second = reviewing.create(spec("Second", parent=project.id, deps=[first.id]))
    service = WorkService(reviewing)
    service.start_execution(first.id)
    finished = service.finish_execution(first.id, 0, {"status": "COMPLETED", "summary": "ok"}, "failed")
    assert (finished.disposition, finished.target) == ("completed", Status.REVIEW)
    assert fake.issues[first.id]["status"] == "In Review"
    assert [s.id for s in service.eligible_specs(project.id)] == [second.id], "review satisfies a dependency"


def test_an_epic_is_complete_only_when_its_specs_are_done_not_in_review(fake, reviewing):
    project = reviewing.create(epic())
    only = reviewing.create(spec("Only", parent=project.id))
    service = WorkService(reviewing)
    service.start_execution(only.id)
    service.finish_execution(only.id, 0, {"status": "COMPLETED", "summary": "ok"}, "failed")
    assert service.epic_progress(project.id)["complete"] is False
    reviewing.transition(only.id, Status.DONE)  # the person approves it
    assert service.epic_progress(project.id)["complete"] is True


def test_finishing_again_after_a_person_marked_it_done_is_a_repeat(fake, reviewing):
    issue = reviewing.create(spec())
    service = WorkService(reviewing)
    service.start_execution(issue.id)
    result = {"status": "COMPLETED", "summary": "ok"}
    assert service.complete_execution(issue.id, result)[2] is False
    reviewing.transition(issue.id, Status.DONE)
    item, _, repeated = service.complete_execution(issue.id, result)
    assert (item.status, repeated) == (Status.DONE, True)


def test_an_owed_review_update_is_satisfied_by_a_spec_already_done(fake, reviewing):
    issue = reviewing.create(spec())
    service = WorkService(reviewing)
    service.start_execution(issue.id)
    reviewing.transition(issue.id, Status.DONE)
    update = {"work_id": issue.id, "target_status": Status.REVIEW, "blocker": None,
              "allowed_from": [Status.IN_PROGRESS], "comments": []}
    service.apply_update(update)
    assert reviewing.get(issue.id).status == Status.DONE, "a finished review is not reopened"


def test_a_project_has_no_review_status(fake, reviewing):
    project = reviewing.create(epic())
    with pytest.raises(InvalidTransition, match="no review status"):
        reviewing.transition(project.id, Status.REVIEW)


@pytest.mark.parametrize("name, message", [("Nope", "no state named"), ("In Progress", "other than"),
                                           ("Todo", "must be a started"), ("Done", "must be a started")])
def test_review_pin_must_be_a_distinct_started_state(name, message):
    with pytest.raises(WorkConfigError, match=message):
        build_status_mapping(STATES, {"status_review": name})


def test_mapping_describes_the_review_state():
    mapping = build_status_mapping(STATES, {"status_review": "In Review"})
    assert mapping.states[Status.REVIEW] == "In Review" and "REVIEW=In Review" in mapping.describe()
    assert mapping.canonical("In Review", "started", []) == Status.REVIEW
    assert mapping.canonical("In Progress", "started", []) == Status.IN_PROGRESS


# --- assignee on claim ---

def test_starting_work_assigns_it_to_the_key_owner(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.IN_PROGRESS)
    assert fake.issues[issue.id]["assignee"] == "me"
    assert fake.writes("save_issue")[-1]["assignee"] == "me"


def test_other_transitions_leave_the_assignee_alone(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.IN_PROGRESS)
    backend.transition(issue.id, Status.DONE)
    assert "assignee" not in fake.writes("save_issue")[-1]


def test_assignee_is_skipped_when_the_server_does_not_publish_it(fake, backend):
    fake.hidden_arguments = {"assignee"}
    issue = backend.create(spec())
    assert backend.transition(issue.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    assert "assignee" not in fake.issues[issue.id]


def test_blocked_uses_label_and_stores_reason(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.IN_PROGRESS)
    blocked = backend.transition(issue.id, Status.BLOCKED, blocker="Need a decision")
    assert blocked.status == Status.BLOCKED and blocked.blocker == "Need a decision"
    assert BLOCKED_LABEL in fake.issues[issue.id]["labels"] and fake.issues[issue.id]["status"] == "Todo"
    assert backend.get(issue.id).blocker == "Need a decision"
    assert backend.get_comments(issue.id)[-1].body == "**Blocked:** Need a decision"


def test_retried_block_does_not_repeat_the_comment(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.BLOCKED, blocker="Why")
    backend.transition(issue.id, Status.BLOCKED, blocker="Why")
    assert len(fake.comments[issue.id]) == 1


def test_leaving_blocked_removes_the_label_and_clears_blocker(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.BLOCKED, blocker="Why")
    ready = backend.transition(issue.id, Status.READY)
    assert ready.status == Status.READY and ready.blocker is None
    assert BLOCKED_LABEL not in fake.issues[issue.id]["labels"]


def test_removing_the_label_in_linear_unblocks(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.BLOCKED, blocker="Why")
    fake.issues[issue.id]["labels"].remove(BLOCKED_LABEL)
    assert backend.get(issue.id).status == Status.READY


def test_done_wins_over_a_stale_blocked_label(fake, backend):
    issue = backend.create(spec())
    backend.transition(issue.id, Status.BLOCKED)
    fake.tool_save_issue(id=issue.id, state="Done")
    assert backend.get(issue.id).status == Status.DONE


def test_native_blocked_state_avoids_the_label(fake):
    states = [*STATES, {"name": "Blocked", "type": "started"}]
    fake.states = states
    backend = LinearWorkBackend(fake, {"team": "AND"})
    issue = backend.create(spec())
    assert backend.transition(issue.id, Status.BLOCKED).status == Status.BLOCKED
    assert fake.issues[issue.id]["status"] == "Blocked"
    assert BLOCKED_LABEL not in fake.issues[issue.id]["labels"]


def test_create_can_start_blocked(fake, backend):
    issue = backend.create(spec(status=Status.BLOCKED))
    assert issue.status == Status.BLOCKED and BLOCKED_LABEL in fake.labels


def test_invalid_and_rejected_transitions(fake, backend):
    issue = backend.create(spec())
    with pytest.raises(InvalidTransition):
        backend.transition(issue.id, "SHIPPED")
    with pytest.raises(WorkItemNotFound):
        backend.transition("AND-404", Status.DONE)
    backend._mapping.states[Status.DONE] = "Nonexistent"
    with pytest.raises(InvalidTransition, match="Could not find state"):
        backend.transition(issue.id, Status.DONE)


def test_unconfirmed_transition_is_not_reported_as_success(fake, backend):
    issue = backend.create(spec())
    original = fake.tool_save_issue
    fake.tool_save_issue = lambda **kwargs: {"id": kwargs["id"]}  # accepted, but nothing changed
    with pytest.raises(InvalidTransition, match="after requesting"):
        backend.transition(issue.id, Status.DONE)
    fake.tool_save_issue = original
    assert fake.issues[issue.id]["status"] == "Todo"


def test_project_status_lifecycle(fake, backend):
    project = backend.create(epic())
    assert backend.transition(project.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    blocked = backend.transition(project.id, Status.BLOCKED, blocker="Paused for legal")
    assert blocked.status == Status.BLOCKED and fake.projects[project.id]["status"]["type"] == "paused"
    assert backend.get(project.id).blocker == "Paused for legal"
    assert backend.transition(project.id, Status.DONE).status == Status.DONE


def test_mapping_override_and_unknown_override():
    mapping = build_status_mapping(STATES, {"status_in_progress": "In Review"})
    assert mapping.states[Status.IN_PROGRESS] == "In Review"
    assert mapping.canonical("In Review", "started", []) == Status.IN_PROGRESS
    with pytest.raises(WorkConfigError, match="status_ready"):
        build_status_mapping(STATES, {"status_ready": "Nope"})


def test_mapping_rejects_a_team_without_distinct_draft_state():
    states = [s for s in STATES if s["type"] != "backlog"]
    with pytest.raises(CapabilityMissing, match="backlog"):
        build_status_mapping(states, {})
    with pytest.raises(CapabilityMissing, match="distinct"):
        build_status_mapping(states, {"status_draft": "Todo"})


def test_mapping_passes_unknown_state_through_as_ineligible():
    assert build_status_mapping(STATES, {}).canonical("Mystery", None, []) == "Mystery"


# --- comments ---

def test_comments_round_trip_oldest_first_with_limit(fake, backend):
    issue = backend.create(spec())
    for text in ("one", "two", "three"):
        backend.add_comment(issue.id, text)
    assert [c.body for c in backend.get_comments(issue.id)] == ["one", "two", "three"]
    latest = backend.get_comments(issue.id, limit=2)
    assert [c.body for c in latest] == ["two", "three"]
    assert latest[0].author == "Bot" and latest[0].work_item_id == issue.id and latest[0].created_at
    assert backend.get_comments(issue.id, limit=0) == []


def test_comment_dedupe_and_hidden_marker(fake, backend):
    issue = backend.create(spec())
    first = backend.add_comment(issue.id, "Finding", dedupe_key="run-1")
    second = backend.add_comment(issue.id, "Finding", dedupe_key="run-1")
    assert first.id == second.id and first.body == "Finding"
    assert len(fake.comments[issue.id]) == 1
    assert "ae-sdlc-op" in fake.comments[issue.id][0]["body"]


def test_lost_comment_response_recovers_without_a_duplicate(fake, backend):
    issue = backend.create(spec())
    fake.lose_response_of.add("save_comment")
    assert backend.add_comment(issue.id, "Finding", dedupe_key="run-1").body == "Finding"
    assert len(fake.comments[issue.id]) == 1


def test_comments_on_a_project_and_unknown_item(fake, backend):
    project = backend.create(epic())
    backend.add_comment(project.id, "Note")
    assert [c.body for c in backend.get_comments(project.id)] == ["Note"]
    with pytest.raises(WorkItemNotFound):
        backend.get_comments("AND-404")


def test_reads_are_retry_safe_and_writes_are_not(fake, backend):
    seen = {}
    original = fake.call

    def spy(tool, arguments, *, retry_safe=False):
        seen.setdefault(tool, set()).add(retry_safe)
        return original(tool, arguments, retry_safe=retry_safe)
    fake.call = spy
    issue = backend.create(spec())
    backend.add_comment(issue.id, "x")
    backend.get_comments(issue.id)
    assert seen["get_issue"] == {True} and seen["list_issues"] == {True} and seen["list_comments"] == {True}
    assert seen["save_issue"] == {False} and seen["save_comment"] == {False} and seen["save_issue_label"] == {False}


# --- pinned project: [linear] project set, epics are issues inside it ---

@pytest.fixture
def pinned(fake):
    fake.tool_save_project(name="Sandbox", addTeams=["AND"])  # created by hand in Linear, not by ae-sdlc
    return LinearWorkBackend(fake, {"team": "AND", "project": "Sandbox"})


def test_pinned_epic_is_a_labelled_issue_and_specs_are_its_sub_issues(fake, pinned):
    epic_item = pinned.create(epic())
    child = pinned.create(spec(parent=epic_item.id))
    standalone = pinned.create(spec("Solo"))
    assert (epic_item.kind, epic_item.parent_id) == ("epic", None) and epic_item.id.startswith("AND-")
    assert fake.writes("save_project") == [] and list(fake.projects) == ["P-AND-1"], "no project is created"
    assert {"ae-sdlc", "ae-sdlc-epic"} <= set(fake.issues[epic_item.id]["labels"])
    sandbox = fake.projects["P-AND-1"]["uuid"]
    assert all(fake.issues[i.id]["projectId"] == sandbox for i in (epic_item, child, standalone))
    assert fake.issues[child.id]["parentId"] == fake.issues[epic_item.id]["uuid"]
    assert pinned.get(child.id).parent_id == epic_item.id, "the parent UUID is reported as its identifier"
    assert standalone.parent_id is None


def test_pinned_lists_specs_of_an_epic_and_everything_in_the_project(fake, pinned):
    epic_item = pinned.create(epic())
    children = [pinned.create(spec(f"S{i}", parent=epic_item.id)) for i in range(3)]
    pinned.create(spec("Solo"))
    fake.tool_save_issue(team="AND", title="Outside", description="", labels=["ae-sdlc"], state="Todo")
    assert [i.id for i in pinned.list_specs(epic_item.id)] == [c.id for c in children]
    listed = pinned.list_items()
    assert "Outside" not in {i.title for i in listed} and len(listed) == 5
    assert {i.kind for i in listed} == {"epic", "spec"}


def test_pinned_project_is_not_an_epic(fake, pinned):
    with pytest.raises(WorkItemNotFound, match="ae-sdlc-epic"):
        pinned.get("P-AND-1")
    assert pinned.list_specs("P-AND-1") == []


def test_pinned_epic_lifecycle_uses_issue_states_and_comments(fake, pinned):
    epic_item = pinned.create(epic())
    assert pinned.transition(epic_item.id, Status.DONE).status == Status.DONE
    assert fake.issues[epic_item.id]["status"] == "Done"
    assert fake.projects["P-AND-1"]["status"]["type"] == "backlog", "the pinned project is never transitioned"
    pinned.add_comment(epic_item.id, "Note")
    assert [c.body for c in pinned.get_comments(epic_item.id)] == ["Note"]


def test_pinned_epic_completes_through_the_service(fake, pinned):
    epic_item = pinned.create(epic())
    child = pinned.create(spec(parent=epic_item.id))
    service = WorkService(pinned)
    assert not service.epic_progress(epic_item.id)["complete"]
    service.start_execution(child.id)
    service.finish_execution(child.id, 0, {"status": "COMPLETED", "summary": "ok"}, "failed")
    assert service.epic_progress(epic_item.id)["complete"]


def test_pinned_repeat_and_lost_response_do_not_duplicate(fake, pinned):
    first = pinned.create(epic())
    assert pinned.create(epic()).id == first.id
    fake.lose_response_of.add("save_issue")
    child = pinned.create(spec(parent=first.id))
    assert child.metadata["reused"] is True
    assert len(fake.issues) == 2


def test_pinned_update_keeps_the_project_and_moves_between_epics(fake, pinned):
    one, two = pinned.create(epic("One")), pinned.create(epic("Two"))
    child = pinned.create(spec(parent=one.id))
    moved = pinned.update(WorkItem(child.id, "spec", child.title, SPEC, child.status, parent_id=two.id))
    assert moved.parent_id == two.id
    assert fake.issues[child.id]["projectId"] == fake.projects["P-AND-1"]["uuid"]
    renamed = pinned.update(WorkItem(one.id, "epic", "Renamed", "New", one.status))
    assert (renamed.kind, renamed.title) == ("epic", "Renamed")


def test_pinned_search_stays_in_the_project(fake, pinned):
    pinned.create(spec("Payments"))
    fake.tool_save_issue(team="AND", title="Payments elsewhere", description="", state="Todo")
    assert [i.title for i in pinned.search("payments")] == ["Payments"]


def test_pinned_does_not_need_project_creation(fake):
    fake.tool_save_project(name="Sandbox", addTeams=["AND"])
    fake.tool_names = [n[5:] for n in dir(FakeLinear) if n.startswith("tool_") and n != "tool_save_project"]
    assert LinearWorkBackend(fake, {"team": "AND", "project": "Sandbox"}).create(epic()).kind == "epic"


def test_pinned_unknown_project_is_reported(fake):
    backend = LinearWorkBackend(fake, {"team": "AND", "project": "Missing"})
    with pytest.raises(ToolFailed, match="Project"):
        backend.list_items() or backend.create(spec())
