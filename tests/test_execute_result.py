"""The execute-work result contract: strict parsing, the schema it mirrors, and verification against git."""
from __future__ import annotations

import json
import shutil
import subprocess
import types
from pathlib import Path

import pytest
from conftest import agent_result, write_spec

from ae_sdlc import installer
from ae_sdlc.work import worktree
from ae_sdlc.work.result import REQUIRED, InvalidResult, parse_execute_result, verify_changes

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def init_repo(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init")


# --- parsing ---------------------------------------------------------------------------------------------

def test_parser_mirrors_the_installed_schema():
    schema = json.loads((installer.DATA / "core" / ".ae-sdlc" / "schemas" / "execute-result.schema.json").read_text())
    assert set(REQUIRED) == set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    for status in schema["properties"]["status"]["enum"]:
        parse_execute_result(agent_result(status))
    with pytest.raises(InvalidResult):
        parse_execute_result(agent_result("DONE"))


def test_a_complete_result_is_returned_unchanged():
    result = agent_result("COMPLETED", summary="ok", changed_files=["a.py"], command_run="pytest",
                          comments=[{"work_item_id": "S-1", "text": "note"}])
    assert parse_execute_result(result) == result


@pytest.mark.parametrize("raw, problem", [
    ([], "not a JSON object"),
    ({"status": "COMPLETED", "summary": "ok", "blockers": []}, "missing field: changed_files"),
    ({**agent_result(), "extra": 1}, "unexpected field: extra"),
    ({**agent_result(), "changed_files": "a.py"}, "changed_files must be a list of strings"),
    ({**agent_result(), "blockers": [1]}, "blockers must be a list of strings"),
    ({**agent_result(), "command_run": 0}, "command_run must be a string or null"),
    ({**agent_result(), "summary": None}, "summary must be a string"),
    ({**agent_result(), "comments": [{"text": "x"}]}, "comments must be a list"),
])
def test_invalid_results_name_the_problem(raw, problem):
    with pytest.raises(InvalidResult, match=problem):
        parse_execute_result(raw)


# --- verification (pure) ---------------------------------------------------------------------------------

def verify(result, actual, ignored=(), root=Path("/repo")):
    return verify_changes(result, actual, lambda p: p in ignored, root)


def test_unchecked_tree_changes_nothing():
    result = agent_result("COMPLETED", changed_files=["a.py"])
    out, v = verify(result, None)
    assert out is result and v.report() == {"checked": False, "reason": v.report()["reason"]}


def test_completed_with_an_unchanged_tree_is_downgraded():
    out, _ = verify(agent_result("COMPLETED"), set())
    assert out["status"] == "FAILED" and "nothing in the working tree changed" in out["blockers"][-1]


def test_claimed_file_that_did_not_change_is_downgraded():
    out, v = verify(agent_result("COMPLETED", changed_files=["a.py", "ghost.py"]), {"a.py"})
    assert out["status"] == "FAILED" and "ghost.py" in out["blockers"][-1]
    assert v.unverified_claims == ["ghost.py"]


def test_paths_are_normalized_and_directories_cover_their_contents():
    claimed = ["./src/a.py", "src\\b.py", "/repo/docs/", "pkg"]
    out, v = verify(agent_result("COMPLETED", changed_files=claimed),
                    {"src/a.py", "src/b.py", "docs/x.md", "pkg/m.py", "uv.lock"})
    assert out["status"] == "COMPLETED"
    assert v.unreported == ["uv.lock"]  # reported, never a reason to fail


def test_ignored_claims_are_not_checkable_and_do_not_fail():
    out, v = verify(agent_result("COMPLETED", changed_files=["a.py", "build/out.js"]), {"a.py"}, ignored={"build/out.js"})
    assert out["status"] == "COMPLETED" and v.ignored_claims == ["build/out.js"]


def test_non_completed_results_are_reported_but_not_changed():
    result = agent_result("BLOCKED", blockers=["pick db"], changed_files=["ghost.py"])
    out, v = verify(result, set())
    assert out is result and v.unverified_claims == ["ghost.py"]


# --- worktree snapshots (real git) ------------------------------------------------------------------------

@needs_git
def test_snapshot_sees_edits_new_files_deletions_and_commits_without_touching_the_index(tmp_path):
    init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "kept.py").write_text("1")
    (tmp_path / "gone.py").write_text("1")
    (tmp_path / "dirty.py").write_text("1")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    (tmp_path / "dirty.py").write_text("already dirty before the run")

    before = worktree.snapshot(tmp_path)
    (tmp_path / "dirty.py").write_text("changed again by the agent")
    (tmp_path / "gone.py").unlink()
    (tmp_path / "new.py").write_text("1")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "x").write_text("1")
    (tmp_path / "committed.py").write_text("1")
    git(tmp_path, "add", "committed.py")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "agent")
    after = worktree.snapshot(tmp_path)

    assert worktree.changed_paths(tmp_path, before, after) == {"dirty.py", "gone.py", "new.py", "committed.py"}
    assert worktree.is_ignored(tmp_path, "ignored/x") and not worktree.is_ignored(tmp_path, "new.py")
    status = git(tmp_path, "status", "--porcelain")  # the real index was never written to
    assert " M dirty.py" in status and "?? new.py" in status and " D gone.py" in status


@needs_git
def test_snapshot_of_a_subdirectory_reports_paths_relative_to_it(tmp_path):
    init_repo(tmp_path)
    sub = tmp_path / "app"
    sub.mkdir()
    before = worktree.snapshot(sub)
    (sub / "a.py").write_text("1")
    (tmp_path / "outside.py").write_text("1")
    assert worktree.changed_paths(sub, before, worktree.snapshot(sub)) == {"a.py"}


def test_snapshot_outside_git_is_none(tmp_path):
    assert worktree.snapshot(tmp_path) is None
    assert worktree.changed_paths(tmp_path, None, None) is None


# --- runner integration -----------------------------------------------------------------------------------

@pytest.fixture
def git_runner(project, load_store, monkeypatch):
    """The installed runner over a git project, with a fake agent that edits files as scripted."""
    import providers
    import run_workflow
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    init_repo(project)
    git(project, "add", ".")
    git(project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "harness")
    monkeypatch.setattr(run_workflow, "ROOT", project)
    monkeypatch.setattr(run_workflow, "RUNTIME", project / ".ae-sdlc-runtime" / "runs")
    monkeypatch.setattr(run_workflow, "SCHEMA", project / ".ae-sdlc" / "schemas" / "execute-result.schema.json")
    plan: dict = {"writes": [], "result": agent_result("COMPLETED", summary="ok")}

    def fake_run(skill, args, tail, schema, model, background=False):
        for rel in plan["writes"]:
            (project / rel).parent.mkdir(parents=True, exist_ok=True)
            (project / rel).write_text("agent wrote this")
        return 0, plan["result"]

    monkeypatch.setattr(providers, "resolve", lambda n=None: "fake")
    monkeypatch.setattr(providers, "load", lambda n: types.SimpleNamespace(run=fake_run, check_cli=lambda: None))
    return types.SimpleNamespace(main=run_workflow.main, plan=plan, project=project)


def _report(project: Path) -> dict:
    return json.loads(next((project / ".ae-sdlc-runtime" / "runs").glob("*-nightly/report.json")).read_text())


def test_runner_accepts_a_completed_result_that_matches_the_tree(git_runner, project, load_store):
    write_spec(project, "SPEC-001")
    git(project, "add", ".")
    git(project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "spec")
    git_runner.plan.update(writes=["src/app.py", "uv.lock"],
                           result=agent_result("COMPLETED", summary="ok", changed_files=["src/app.py"]))
    assert git_runner.main(["nightly", "SPEC-001"]) == 0
    assert load_store.WorkStore(project).get("SPEC-001").status == "DONE"
    verification = _report(project)["completed"][0]["verification"]
    assert verification == {"checked": True, "actual_changes": 2, "unreported": ["uv.lock"]}


def test_runner_blocks_a_completed_result_with_no_changes(git_runner, project, load_store):
    write_spec(project, "SPEC-001")
    git_runner.plan["result"] = agent_result("COMPLETED", summary="ok", changed_files=["src/app.py"])
    assert git_runner.main(["nightly", "SPEC-001"]) == 1
    spec = load_store.WorkStore(project).get("SPEC-001")
    assert spec.status == "BLOCKED"
    failed = _report(project)["failed"][0]
    assert "nothing in the working tree changed" in failed["reason"] and failed["verification"]["unverified_claims"] == ["src/app.py"]


def test_runner_blocks_an_invalid_result_from_a_prompt_only_provider(git_runner, project, load_store):
    write_spec(project, "SPEC-001")
    git_runner.plan["result"] = {"status": "DONE", "summary": "trust me"}
    assert git_runner.main(["nightly", "SPEC-001"]) == 1
    assert load_store.WorkStore(project).get("SPEC-001").status == "BLOCKED"
    failed = _report(project)["failed"][0]
    assert "invalid structured result" in failed["reason"] and "status must be one of" in failed["reason"]
    raw = next((project / ".ae-sdlc-runtime" / "runs").glob("*-nightly/SPEC-001.json"))
    assert json.loads(raw.read_text()) == {"status": "DONE", "summary": "trust me"}  # kept for debugging
