"""Opt-in smoke run of the whole workflow on real trackers, with a scripted agent. It leaves its work there.

Linear: LINEAR_API_KEY and AE_SDLC_LINEAR_LIVE_TEAM; AE_SDLC_LINEAR_LIVE_PROJECT also pins a project.
Jira: Atlassian credentials, AE_SDLC_JIRA_LIVE_CLOUD_ID and AE_SDLC_JIRA_LIVE_PROJECT.
Set AE_SDLC_E2E_METRICS_FILE to also append the run's evaluation metrics to a JSON Lines file.
"""
from __future__ import annotations

import json
import os
import time
import types
import uuid
from contextlib import contextmanager

import pytest

from ae_sdlc.work.jira import JiraWorkBackend
from ae_sdlc.work.linear import LinearWorkBackend
from ae_sdlc.work.mcp import auth
from ae_sdlc.work.mcp.client import McpClient, provider_endpoint
from ae_sdlc.work.models import Status
from ae_sdlc.work.publish import publish
from ae_sdlc.work.runner import run_workflow
from ae_sdlc.work.service import WorkService

TEAM = os.environ.get("AE_SDLC_LINEAR_LIVE_TEAM")
LINEAR_PROJECT = os.environ.get("AE_SDLC_LINEAR_LIVE_PROJECT")
CLOUD = os.environ.get("AE_SDLC_JIRA_LIVE_CLOUD_ID")
PROJECT = os.environ.get("AE_SDLC_JIRA_LIVE_PROJECT")
TARGETS = {
    "linear": (TEAM, LinearWorkBackend, {"team": TEAM, **({"project": LINEAR_PROJECT} if LINEAR_PROJECT else {})}),
    "jira": (CLOUD and PROJECT, JiraWorkBackend, {
        "cloud_id": CLOUD, "project_key": PROJECT, "status_draft": "To Do", "status_ready": "To Do",
        "status_in_progress": "In Progress", "status_done": "Done", "status_blocked": "Blocked"}),
}
BODY = "## Goal\n\nLive end-to-end smoke — Привіт.\n\n- [ ] Accept\n"


class Timed:
    """An MCP client that records how long each call took."""

    def __init__(self, client):
        self.client, self.seconds = client, []

    def call(self, *args, **kwargs):
        started = time.monotonic()
        try:
            return self.client.call(*args, **kwargs)
        finally:
            self.seconds.append(time.monotonic() - started)

    def __getattr__(self, name):
        return getattr(self.client, name)


@pytest.mark.parametrize("name", TARGETS)
def test_smoke_prepare_execute_and_report(name, project):
    enabled, backend_type, settings = TARGETS[name]
    if not enabled:
        pytest.skip(f"set the live {name} variables to run against the real tracker")
    tag = uuid.uuid4().hex[:8]
    credential = auth.resolve(name)
    timed: list[Timed] = []

    @contextmanager
    def services():
        with McpClient(provider_endpoint(name), credential) as client:
            measured = Timed(client)
            timed.append(measured)
            yield WorkService(backend_type(measured, settings), root=project)

    doc = {"epic": {"title": f"ae-sdlc e2e {tag}", "description": BODY, "status": "READY"},
           "specs": [{"ref": "a", "title": f"first {tag}", "description": BODY, "status": "READY"},
                     {"ref": "b", "title": f"second {tag}", "description": BODY, "status": "READY", "depends_on": ["a"]}]}
    with services() as service:
        first = publish(service.backend, doc)
        again = publish(service.backend, doc)
        epic_id = first["epic"]["id"]
        spec_ids = [s["id"] for s in first["specs"]]
        service.backend.add_comment(spec_ids[0], "Live context for the agent")
    assert [s["id"] for s in again["specs"]] == spec_ids and all(s["reused"] for s in again["specs"])

    agent_runs, prompts = [], {}

    def agent(skill, args, tail, schema, run_model, background=False):
        agent_runs.append(args)
        prompts[args] = tail
        return 0, {"status": "COMPLETED", "summary": f"smoke {tag}", "blockers": []}

    adapter = types.SimpleNamespace(run=agent, check_cli=lambda: None)
    providers = types.SimpleNamespace(resolve=lambda n=None: "fake", load=lambda n: adapter, BACKGROUND_TAIL="runner_owns_status=true")
    assert run_workflow(project, ["nightly", epic_id], providers, services) == 0

    with services() as service:
        statuses = {i: service.backend.get(i).status for i in [epic_id, *spec_ids]}
        listed = service.backend.list_specs(epic_id)
        comments = service.backend.get_comments(spec_ids[0])
    report = json.loads(next((project / ".ae-sdlc-runtime" / "runs").glob("*/report.json")).read_text(encoding="utf-8"))

    bodies = [c.body for c in comments]
    seconds = [s for t in timed for s in t.seconds]
    metrics = {
        "backend": name, "tag": tag,
        "items_created": 1 + len(spec_ids), "lookups_ok": len(listed) == len(spec_ids),
        "duplicates": len(listed) - len(spec_ids),
        "status_transitions_ok": all(s == Status.DONE for s in statuses.values()),
        "reconciliation_failures": report["pending_updates_total"],
        "unexpected_retries": len(agent_runs) - len(spec_ids),
        "duplicate_comments": len(bodies) - len(set(bodies)),
        "tracker_calls": len(seconds), "connections": len(timed),
        "latency_mean_ms": round(1000 * sum(seconds) / len(seconds)),
        "latency_max_ms": round(1000 * max(seconds)),
    }
    print(json.dumps(metrics))
    if path := os.environ.get("AE_SDLC_E2E_METRICS_FILE"):
        with open(path, "a", encoding="utf-8") as output:
            output.write(json.dumps(metrics) + "\n")

    assert agent_runs == spec_ids and "Live context for the agent" in prompts[spec_ids[0]]
    assert metrics["status_transitions_ok"] and report["remote_edits"] == [], (statuses, report["remote_edits"])
    assert (metrics["duplicates"], metrics["unexpected_retries"], metrics["duplicate_comments"]) == (0, 0, 0)
