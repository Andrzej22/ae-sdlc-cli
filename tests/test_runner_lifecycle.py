"""Characterization tests for run_workflow.py using a fake provider adapter (no agent CLI, no network)."""
from __future__ import annotations

import json
import types

import pytest

from conftest import write_spec


@pytest.fixture
def runner(project, load_store, monkeypatch):
    import providers
    import run_workflow
    monkeypatch.setattr(run_workflow, "ROOT", project)
    monkeypatch.setattr(run_workflow, "RUNTIME", project / ".ae-sdlc-runtime" / "runs")
    monkeypatch.setattr(run_workflow, "SCHEMA", project / ".ae-sdlc" / "schemas" / "execute-result.schema.json")
    calls: list[str] = []
    script: dict[str, tuple[int, dict | None]] = {}

    def fake_run(skill, args, tail, schema, model, background=False):
        calls.append(args)
        assert skill == "execute-work" and background is True
        assert "runner_owns_status=true" in tail
        return script.get(args, (0, {"status": "COMPLETED", "summary": "ok", "blockers": []}))

    adapter = types.SimpleNamespace(run=fake_run, check_cli=lambda: None)
    monkeypatch.setattr(providers, "resolve", lambda n=None: "fake")
    monkeypatch.setattr(providers, "load", lambda n: adapter)
    return types.SimpleNamespace(main=run_workflow.main, calls=calls, script=script, project=project)


def status_of(project, load_store, sid):
    return load_store.WorkStore(project).get(sid).status


def test_runs_ready_specs_in_dependency_order_and_marks_done(runner, project, load_store):
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-011", epic="EPIC-001", deps=["SPEC-010"])
    write_spec(project, "SPEC-010", epic="EPIC-001")
    assert runner.main(["nightly", "EPIC-001"]) == 0
    assert runner.calls == ["SPEC-010", "SPEC-011"]  # dependent runs after its dependency within one run
    assert status_of(project, load_store, "SPEC-011") == "DONE"


def test_two_runs_in_the_same_second_get_separate_directories(runner, project, monkeypatch):
    import time
    monkeypatch.setattr(time, "strftime", lambda fmt: "20260101-000000")
    write_spec(project, "SPEC-001", status="DRAFT")
    runner.main(["nightly", "SPEC-001"])
    runner.main(["nightly", "SPEC-001"])
    assert len(list((project / ".ae-sdlc-runtime" / "runs").glob("20260101-000000-*"))) == 2


def test_execution_report_written_under_runtime(runner, project, capsys):
    write_spec(project, "SPEC-001")
    runner.main(["nightly", "SPEC-001"])
    reports = list((project / ".ae-sdlc-runtime" / "runs").glob("*-nightly/report.json"))
    assert len(reports) == 1
    rep = json.loads(reports[0].read_text())
    assert [c["id"] for c in rep["completed"]] == ["SPEC-001"]
    assert "run_report=" in capsys.readouterr().out


def test_blocked_and_needs_input_become_BLOCKED_with_blocker(runner, project, load_store):
    write_spec(project, "SPEC-001")
    runner.script["SPEC-001"] = (0, {"status": "NEEDS_INPUT", "blockers": ["pick db"], "summary": ""})
    assert runner.main(["nightly", "SPEC-001"]) == 0
    s = load_store.WorkStore(project).get("SPEC-001")
    assert s.status == "BLOCKED" and s.meta["blocker"] == "pick db"


def test_failed_result_is_BLOCKED_and_reported_as_failed(runner, project, load_store):
    """Decision D2, revisited in PR 6: the status is BLOCKED on every backend; the reason marks the failure."""
    write_spec(project, "SPEC-001")
    runner.script["SPEC-001"] = (0, {"status": "FAILED", "blockers": ["boom"], "summary": ""})
    assert runner.main(["nightly", "SPEC-001"]) == 1
    assert status_of(project, load_store, "SPEC-001") == "BLOCKED"


def test_completed_with_nonzero_exit_is_not_done(runner, project, load_store):
    write_spec(project, "SPEC-001")
    runner.script["SPEC-001"] = (3, {"status": "COMPLETED", "blockers": [], "summary": "x"})
    assert runner.main(["nightly", "SPEC-001"]) == 1
    assert status_of(project, load_store, "SPEC-001") == "BLOCKED"


def test_missing_structured_result_is_failed(runner, project, load_store):
    write_spec(project, "SPEC-001")
    runner.script["SPEC-001"] = (1, None)
    assert runner.main(["nightly", "SPEC-001"]) == 1
    assert "exited 1" in load_store.WorkStore(project).get("SPEC-001").meta["blocker"]


def test_each_spec_attempted_at_most_once_and_max_specs_honored(runner, project):
    write_spec(project, "EPIC-001", kind="epic")
    for i in (1, 2, 3):
        write_spec(project, f"SPEC-00{i}", epic="EPIC-001")
    runner.main(["nightly", "EPIC-001", "--max-specs", "2"])
    assert runner.calls == ["SPEC-001", "SPEC-002"]


def test_continue_on_failure_false_stops_run(runner, project):
    wf = project / ".ae-sdlc" / "workflows" / "nightly.toml"
    wf.write_text(wf.read_text().replace("continue_on_failure = true", "continue_on_failure = false"))
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-001", epic="EPIC-001")
    write_spec(project, "SPEC-002", epic="EPIC-001")
    runner.script["SPEC-001"] = (0, {"status": "BLOCKED", "blockers": ["x"], "summary": ""})
    runner.main(["nightly", "EPIC-001"])
    assert runner.calls == ["SPEC-001"]


def test_draft_blocked_and_unmet_dependency_never_run(runner, project):
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-001", epic="EPIC-001", status="DRAFT")
    write_spec(project, "SPEC-002", epic="EPIC-001", status="BLOCKED")
    write_spec(project, "SPEC-003", epic="EPIC-001", deps=["SPEC-001"])
    runner.main(["nightly", "EPIC-001"])
    assert runner.calls == []


def test_parallelism_other_than_one_rejected(runner, project):
    wf = project / ".ae-sdlc" / "workflows" / "nightly.toml"
    wf.write_text(wf.read_text().replace("parallelism = 1", "parallelism = 2"))
    with pytest.raises(SystemExit):
        runner.main(["nightly", "SPEC-001"])


def test_in_progress_spec_is_not_rerun(runner, project):
    """A spec left IN_PROGRESS by a crashed run waits for an explicit `work recover --requeue`."""
    write_spec(project, "SPEC-001", status="IN_PROGRESS")
    runner.main(["nightly", "SPEC-001"])
    assert runner.calls == []


def test_refuses_unattended_run_without_a_declared_sandbox(runner, project, load_store, monkeypatch):
    monkeypatch.delenv("AE_SDLC_SANDBOX")
    write_spec(project, "SPEC-001")
    with pytest.raises(SystemExit, match="outside a sandbox"):
        runner.main(["nightly", "SPEC-001"])
    assert runner.calls == []  # fail closed: no agent started, nothing claimed
    assert status_of(project, load_store, "SPEC-001") == "READY"


def test_explicit_opt_out_runs_and_is_recorded_in_the_report(runner, project, monkeypatch, capsys):
    monkeypatch.delenv("AE_SDLC_SANDBOX")
    write_spec(project, "SPEC-001")
    assert runner.main(["nightly", "SPEC-001", "--allow-unsandboxed"]) == 0
    assert "without a sandbox" in capsys.readouterr().err
    report = next((project / ".ae-sdlc-runtime" / "runs").glob("*-nightly/report.json"))
    assert json.loads(report.read_text())["sandbox"] == "unsandboxed"


def test_entrypoint_passes_the_opt_out_flag_through(project, monkeypatch):
    from conftest import run_entry
    import os
    write_spec(project, "SPEC-001", status="DRAFT")
    env = {k: v for k, v in os.environ.items() if k != "AE_SDLC_SANDBOX"}
    refused = run_entry(project, "--provider", "claude", "workflow", "nightly", "SPEC-001", env=env)
    assert refused.returncode != 0 and "outside a sandbox" in refused.stderr
    env["PATH"] = ""  # no agent CLI: getting past the gate surfaces as a missing-CLI error instead
    allowed = run_entry(project, "--provider", "claude", "workflow", "nightly", "SPEC-001",
                        "--allow-unsandboxed", env=env)
    assert "outside a sandbox" not in allowed.stderr and "CLI not found" in allowed.stderr
