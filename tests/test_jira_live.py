"""Opt-in round trip against a real Jira project; it creates work there and does not delete it.

Run with Atlassian credentials set, AE_SDLC_JIRA_LIVE_CLOUD_ID naming the site, and
AE_SDLC_JIRA_LIVE_PROJECT naming a disposable project.
"""
from __future__ import annotations

import os
import uuid

import pytest

from ae_sdlc.work.backend import WorkBackend, WorkItemNotFound
from ae_sdlc.work.jira import JiraWorkBackend
from ae_sdlc.work.mcp import auth
from ae_sdlc.work.mcp.client import McpClient, provider_endpoint
from ae_sdlc.work.models import Status, WorkItem

CLOUD = os.environ.get("AE_SDLC_JIRA_LIVE_CLOUD_ID")
PROJECT = os.environ.get("AE_SDLC_JIRA_LIVE_PROJECT")
pytestmark = pytest.mark.skipif(not (CLOUD and PROJECT), reason="set AE_SDLC_JIRA_LIVE_CLOUD_ID and _PROJECT")

SPEC = """## Goal and Outcome

Live round trip — Привіт.

## Acceptance Criteria

- [ ] One

| a | b |
|---|---|
| 1 | 2 |

```python
print("x")
```
"""


def test_full_lifecycle_against_real_jira():
    tag = uuid.uuid4().hex[:8]
    with McpClient(provider_endpoint("jira"), auth.resolve("jira")) as client:
        backend = JiraWorkBackend(client, {"cloud_id": CLOUD, "project_key": PROJECT, "status_draft": "To Do", "status_ready": "To Do",
                                          "status_in_progress": "In Progress", "status_done": "Done",
                                          "status_blocked": "Blocked"})
        assert isinstance(backend, WorkBackend)

        epic = backend.create(WorkItem("", "epic", f"ae-sdlc live {tag}", "Epic body", Status.DRAFT), f"{tag}-epic")
        assert epic.id and epic.kind == "epic" and epic.status == Status.DRAFT and epic.metadata["managed"]

        first = backend.create(WorkItem("", "spec", f"first {tag}", SPEC, Status.READY, parent_id=epic.id), f"{tag}-1")
        assert first.status == Status.READY and first.parent_id == epic.id
        for fragment in ("Привіт", 'print("x")', "Acceptance Criteria"):
            assert fragment in first.description

        second = backend.create(WorkItem("", "spec", f"second {tag}", SPEC, Status.READY, parent_id=epic.id,
                                         dependencies=[first.id]), f"{tag}-2")
        assert backend.get(second.id).dependencies == [first.id]
        assert backend.get(first.id).dependencies == []
        assert [i.id for i in backend.list_specs(epic.id, Status.READY)] == [first.id, second.id]

        noted = backend.add_comment(first.id, "Live note", dedupe_key=tag)
        assert backend.add_comment(first.id, "Live note", dedupe_key=tag).id == noted.id
        assert [c.body for c in backend.get_comments(first.id)] == ["Live note"]

        assert backend.transition(first.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
        blocked = backend.transition(first.id, Status.BLOCKED, blocker="Live blocker")
        assert blocked.status == Status.BLOCKED and backend.get(first.id).blocker == "Live blocker"
        assert backend.transition(first.id, Status.READY).status == Status.READY
        assert backend.transition(first.id, Status.DONE).status == Status.DONE

        assert backend.create(WorkItem("", "spec", f"first {tag}", SPEC, Status.READY), f"{tag}-1").id == first.id
        with pytest.raises(WorkItemNotFound):
            backend.get(f"{PROJECT}-99999999")
