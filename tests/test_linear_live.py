"""Opt-in round trip against a real Linear team; it creates work there and does not delete it.

Run with LINEAR_API_KEY set and AE_SDLC_LINEAR_LIVE_TEAM naming a disposable team.
"""
from __future__ import annotations

import os
import uuid

import pytest

from ae_sdlc.work.backend import WorkBackend
from ae_sdlc.work.linear import LinearWorkBackend
from ae_sdlc.work.mcp import auth
from ae_sdlc.work.mcp.client import McpClient, provider_endpoint
from ae_sdlc.work.models import Status, WorkItem

TEAM = os.environ.get("AE_SDLC_LINEAR_LIVE_TEAM")
pytestmark = pytest.mark.skipif(not TEAM, reason="set AE_SDLC_LINEAR_LIVE_TEAM to run against real Linear")

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


def test_full_lifecycle_against_real_linear():
    tag = uuid.uuid4().hex[:8]
    with McpClient(provider_endpoint("linear"), auth.resolve("linear")) as client:
        backend = LinearWorkBackend(client, {"team": TEAM})
        assert isinstance(backend, WorkBackend)

        epic = backend.create(WorkItem("", "epic", f"ae-sdlc live {tag}", "Epic body", Status.DRAFT))
        assert epic.id and epic.status == Status.DRAFT and epic.metadata["managed"]
        assert backend.create(WorkItem("", "epic", f"ae-sdlc live {tag}", "Epic body", Status.DRAFT)).id == epic.id

        first = backend.create(WorkItem("", "spec", f"first {tag}", SPEC, Status.READY, parent_id=epic.id))
        assert first.status == Status.READY and first.parent_id and first.metadata["managed"]
        for fragment in ("Привіт", "print(\"x\")", "Acceptance Criteria"):
            assert fragment in first.description
        assert "ae-sdlc-op" not in first.description, "the operation footer must parse back after normalization"
        assert first.metadata["op_key"]
        repeat = backend.create(WorkItem("", "spec", f"first {tag}", SPEC, Status.READY, parent_id=epic.id))
        assert repeat.id == first.id and repeat.metadata["reused"]

        second = backend.create(WorkItem("", "spec", f"second {tag}", SPEC, Status.READY, parent_id=epic.id,
                                         dependencies=[first.id]))
        assert backend.get(second.id).dependencies == [first.id]
        assert [i.id for i in backend.list_specs(epic.id, Status.READY)] == [first.id, second.id]
        assert backend.get(backend.get(first.id).parent_id).id == epic.id

        noted = backend.add_comment(first.id, "Live note", dedupe_key=tag)
        assert backend.add_comment(first.id, "Live note", dedupe_key=tag).id == noted.id
        assert [c.body for c in backend.get_comments(first.id)] == ["Live note"]

        assert backend.transition(first.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
        blocked = backend.transition(first.id, Status.BLOCKED, blocker="Live blocker")
        assert blocked.status == Status.BLOCKED and backend.get(first.id).blocker == "Live blocker"
        assert backend.transition(first.id, Status.READY).status == Status.READY
        assert backend.transition(first.id, Status.DONE).status == Status.DONE

        assert backend.transition(epic.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
        assert any(i.id == second.id for i in backend.list_items())
        assert any(i.id == first.id for i in backend.search(f"first {tag}"))
