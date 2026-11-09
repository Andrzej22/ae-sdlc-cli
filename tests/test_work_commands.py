"""PR 5: the `./ae-sdlc work` contract on the local and remote paths, and the provider skill copies."""
from __future__ import annotations

import json
import types
import os
import re
import stat
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import write_spec
from test_linear_backend import FakeLinear

from ae_sdlc import backend_cli, cli, installer
from ae_sdlc.work import commands
from ae_sdlc.work.backend import InvalidInput
from ae_sdlc.work.linear import LinearWorkBackend
from ae_sdlc.work.mcp.errors import AuthRejected, RateLimited, TransportFailure
from ae_sdlc.work.publish import publish
from ae_sdlc.work.service import WorkService

ALL = list(installer.PROVIDERS)
BODY = "## Goal\n\nПривіт, specs with `backticks` and \"quotes\".\n"


def work(project: Path, *args: str, stdin: str = "", env=None):
    proc = subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), "work", *args], cwd=project, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", env=env)
    return proc.returncode, json.loads(proc.stdout) if proc.stdout.strip() else None


def status_of(project: Path, work_id: str) -> str:
    return work(project, "get", work_id)[1]["item"]["status"]


# --- local path, through the real generated entrypoint ---


def test_publish_allocates_ids_and_writes_the_existing_local_format(project):
    write_spec(project, "SPEC-007", status="DONE")
    doc = {"epic": {"title": "Billing", "description": BODY, "status": "READY"},
           "specs": [{"ref": "a", "title": "API", "description": BODY, "status": "READY"},
                     {"ref": "b", "title": "UI", "description": BODY, "status": "READY", "depends_on": ["a", "SPEC-007"]}]}
    rc, out = work(project, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 0 and out["ok"]
    assert out["epic"]["id"] == "EPIC-001"
    assert [s["id"] for s in out["specs"]] == ["SPEC-008", "SPEC-009"]
    item = work(project, "get", "SPEC-009")[1]["item"]
    assert item["parent_id"] == "EPIC-001" and item["dependencies"] == ["SPEC-008", "SPEC-007"]
    assert "Привіт" in item["description"]
    text = Path(out["specs"][1]["path"]).read_text(encoding="utf-8")
    assert text.startswith("---\nartifact_type: spec\nwork_item_id: SPEC-009\n")


def test_publish_retry_does_not_duplicate(project):
    doc = json.dumps({"specs": [{"title": "One", "description": BODY}]})
    first = work(project, "publish", "--json-stdin", stdin=doc)[1]["specs"][0]
    again = work(project, "publish", "--json-stdin", stdin=doc)[1]["specs"][0]
    assert (first["id"], first["reused"]) == ("SPEC-001", False)
    assert (again["id"], again["reused"]) == ("SPEC-001", True)


def test_blocking_questions_keep_specs_out_of_ready(project):
    doc = {"specs": [{"title": "One", "description": BODY, "status": "READY"}], "blocking_questions": ["Which plan?"]}
    out = work(project, "publish", "--json-stdin", stdin=json.dumps(doc))[1]
    assert out["specs"][0]["status"] == "DRAFT"
    assert out["blocking_questions"] == ["Which plan?"]


def test_publish_updates_existing_work_and_promotes_it(project):
    write_spec(project, "SPEC-001", status="DRAFT", title="Old")
    doc = {"specs": [{"id": "SPEC-001", "title": "New", "status": "READY"}]}
    assert work(project, "publish", "--json-stdin", stdin=json.dumps(doc))[0] == 0
    item = work(project, "get", "SPEC-001")[1]["item"]
    assert (item["title"], item["status"]) == ("New", "READY")
    assert "Text." in item["description"]


def test_publish_cannot_reopen_finished_work(project):
    write_spec(project, "SPEC-001", status="DONE")
    doc = {"specs": [{"id": "SPEC-001", "status": "READY"}]}
    rc, out = work(project, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 5 and out["error"]["code"] == "invalid_transition"
    assert status_of(project, "SPEC-001") == "DONE"


@pytest.mark.parametrize("doc, fragment", [
    ({}, "needs an epic"),
    ({"specs": [{"title": "x"}]}, "title and a description"),
    ({"specs": [{"title": "x", "description": "y", "status": "DONE"}]}, "DRAFT or READY"),
    ({"specs": [{"title": "x", "description": "y", "dependencies": []}]}, "unknown field"),
    ({"specs": [{"ref": "a", "title": "x", "description": "y", "depends_on": ["b"]},
                {"ref": "b", "title": "z", "description": "y"}]}, "listed before"),
])
def test_invalid_documents_write_nothing(project, doc, fragment):
    rc, out = work(project, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 2 and out["error"]["code"] == "invalid_input" and fragment in out["error"]["message"]
    assert work(project, "lookup", "x")[1]["items"] == []


def test_publish_checks_external_dependencies_before_writing(project):
    doc = {"specs": [{"title": "New", "description": "y", "depends_on": ["SPEC-404"]}]}
    assert work(project, "publish", "--json-stdin", stdin=json.dumps(doc))[0] == 3
    assert work(project, "lookup", "New")[1]["items"] == []


def test_payload_from_a_file(project):
    path = project / ".ae-sdlc-runtime" / "p.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"specs": [{"title": "One", "description": BODY}]}), encoding="utf-8")
    assert work(project, "publish", "--json-file", str(path))[0] == 0
    assert work(project, "publish", "--json-file", "missing.json")[0] == 2


def test_start_returns_context_and_marks_in_progress(project):
    write_spec(project, "EPIC-001", kind="epic", status="READY")
    write_spec(project, "SPEC-000", status="DONE")
    write_spec(project, "SPEC-001", epic="EPIC-001", deps=["SPEC-000"])
    rc, out = work(project, "start", "SPEC-001")
    assert rc == 0 and out["resumed"] is False
    assert out["item"]["status"] == "IN_PROGRESS" and out["item"]["revision"]
    assert out["epic"]["id"] == "EPIC-001"
    assert [(d["id"], d["status"]) for d in out["dependencies"]] == [("SPEC-000", "DONE")]
    assert out["comments"] == [] and out["related"] == []
    assert status_of(project, "SPEC-001") == "IN_PROGRESS"


def test_start_retry_is_reported_as_resumed(project):
    write_spec(project, "SPEC-001")
    work(project, "start", "SPEC-001")
    rc, out = work(project, "start", "SPEC-001")
    assert rc == 0 and out["resumed"] is True


@pytest.mark.parametrize("status", ["DRAFT", "BLOCKED", "DONE"])
def test_start_refuses_work_that_is_not_ready(project, status):
    write_spec(project, "SPEC-001", status=status)
    assert work(project, "start", "SPEC-001")[0] == 5
    assert status_of(project, "SPEC-001") == status


def test_start_refuses_unfinished_or_dangling_dependencies(project):
    write_spec(project, "SPEC-001", status="READY")
    write_spec(project, "SPEC-002", deps=["SPEC-001", "SPEC-404"])
    rc, out = work(project, "start", "SPEC-002")
    assert rc == 5 and "SPEC-001, SPEC-404" in out["error"]["message"]
    assert status_of(project, "SPEC-002") == "READY"


def test_start_refuses_an_epic(project):
    write_spec(project, "EPIC-001", kind="epic", status="READY")
    assert work(project, "start", "EPIC-001")[0] == 5


def test_start_accepts_a_path_and_keeps_compatibility(project):
    path = write_spec(project, "SPEC-001")
    rc, out = work(project, "start", str(path.relative_to(project)))
    assert rc == 0 and out["item"]["id"] == "SPEC-001"
    assert status_of(project, "SPEC-001") == "IN_PROGRESS"


def test_context_changes_nothing(project):
    write_spec(project, "SPEC-001")
    assert work(project, "context", "SPEC-001")[1]["item"]["status"] == "READY"
    assert status_of(project, "SPEC-001") == "READY"


@pytest.mark.parametrize("outcome, status, disposition", [
    ("COMPLETED", "DONE", "completed"), ("BLOCKED", "BLOCKED", "blocked"),
    ("NEEDS_INPUT", "BLOCKED", "blocked"), ("FAILED", "BLOCKED", "failed"), ("PARTIAL", "BLOCKED", "failed"),
])
def test_finish_applies_the_outcome_and_a_retry_is_harmless(project, outcome, status, disposition):
    write_spec(project, "SPEC-001")
    work(project, "start", "SPEC-001")
    result = json.dumps({"status": outcome, "summary": "s", "blockers": []})
    rc, out = work(project, "finish", "SPEC-001", "--result-stdin", stdin=result)
    assert rc == 0 and (out["status"], out["disposition"], out["repeated"]) == (status, disposition, False)
    rc, out = work(project, "finish", "SPEC-001", "--result-stdin", stdin=result)
    assert rc == 0 and (out["disposition"], out["repeated"]) == (disposition, True)


def test_finish_requires_a_started_spec(project):
    write_spec(project, "SPEC-001")
    rc, _ = work(project, "finish", "SPEC-001", "--result-stdin", stdin='{"status": "COMPLETED"}')
    assert rc == 5 and status_of(project, "SPEC-001") == "READY"


@pytest.mark.parametrize("raw", ["not json", "[]", '{"status": "DONE"}', '{"status": "BLOCKED", "blockers": "x"}'])
def test_finish_rejects_a_malformed_result_without_a_change(project, raw):
    write_spec(project, "SPEC-001", status="IN_PROGRESS")
    assert work(project, "finish", "SPEC-001", "--result-stdin", stdin=raw)[0] == 2
    assert status_of(project, "SPEC-001") == "IN_PROGRESS"


def test_list_and_lookup_return_summaries(project):
    write_spec(project, "EPIC-001", kind="epic", status="READY", title="Billing")
    write_spec(project, "SPEC-001", epic="EPIC-001", status="DONE", title="Invoices")
    write_spec(project, "SPEC-002", epic="EPIC-001", deps=["SPEC-001"])
    write_spec(project, "SPEC-003", epic="EPIC-001", deps=["SPEC-404"])
    assert [i["id"] for i in work(project, "list", "EPIC-001")[1]["items"]] == ["SPEC-001", "SPEC-002", "SPEC-003"]
    assert [i["id"] for i in work(project, "list", "EPIC-001", "--status", "READY", "--eligible")[1]["items"]] == ["SPEC-002"]
    found = work(project, "lookup", "invoice")[1]["items"]
    assert [i["id"] for i in found] == ["SPEC-001"] and "description" not in found[0]


def test_unknown_item_and_scope_are_not_found(project):
    for args in (["get", "SPEC-404"], ["list", "EPIC-404"], ["start", "SPEC-404"]):
        rc, out = work(project, *args)
        assert rc == 3 and out["error"]["code"] == "not_found"


def test_comment_on_local_reports_the_missing_capability(project):
    write_spec(project, "SPEC-001")
    rc, out = work(project, "comment", "SPEC-001", "--text-stdin", stdin="found something")
    assert rc == 6 and out["error"]["code"] == "capability_missing"


def test_usage_errors_are_json(project):
    rc, out = work(project, "nonsense")
    assert rc == 2 and out["error"]["code"] == "invalid_input"
    rc, out = work(project, "publish")
    assert rc == 2 and "required" in out["error"]["message"]


def test_help_needs_no_backend(project):
    proc = subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), "work", "--help"], cwd=project,
                          capture_output=True, text=True)
    assert proc.returncode == 0 and "publish" in proc.stdout


# --- delegation to the installed package ---


def select_backend(project: Path, name: str) -> None:
    (project / ".ae-sdlc" / "work.toml").write_text(
        f'[work]\nbackend = "{name}"\n\n[linear]\nteam = "Engineering"\n', encoding="utf-8")


def test_remote_backend_delegates_arguments_and_stdin(project, tmp_path):
    select_backend(project, "linear")
    stub = tmp_path / "stub"
    stub.write_text("#!/bin/sh\nprintf '{\"argv\": \"%s\", \"stdin\": \"%s\"}' \"$*\" \"$(cat)\"\nexit 4\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    rc, out = work(project, "comment", "ENG-1", "--text-stdin", stdin="hello",
                   env={"AE_SDLC_BIN": str(stub), "PATH": os.environ["PATH"]})
    assert rc == 4
    assert out == {"argv": f"_work --root {project} work comment ENG-1 --text-stdin", "stdin": "hello"}


def test_remote_backend_without_the_package_is_a_json_error(project, tmp_path):
    select_backend(project, "linear")
    rc, out = work(project, "get", "ENG-1", env={"PATH": str(tmp_path)})
    assert rc == 6 and "installed ae-sdlc package" in out["error"]["message"]


def test_status_delegates_for_a_remote_backend(project, tmp_path):
    select_backend(project, "jira")
    stub = tmp_path / "stub"
    stub.write_text("#!/bin/sh\necho \"$*\"\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    proc = subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), "status", "--ready"], cwd=project,
                          capture_output=True, text=True, env={"AE_SDLC_BIN": str(stub), "PATH": ""})
    assert proc.stdout.strip() == f"_work --root {project} status --status READY"


# --- remote path, through the package entrypoint with a fake Linear ---


@pytest.fixture
def linear(project, monkeypatch):
    select_backend(project, "linear")
    fake = FakeLinear()

    @contextmanager
    def client(url, credential):
        yield fake

    monkeypatch.setattr(backend_cli, "McpClient", client)
    monkeypatch.setattr(backend_cli.auth, "resolve", lambda provider: object())
    return project, fake


def remote(project, capsys, monkeypatch, *args, stdin=""):
    import io
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = cli.main(["_work", "--root", str(project), "work", *args])
    return rc, json.loads(capsys.readouterr().out)


def test_remote_lifecycle_never_touches_docs_product(linear, capsys, monkeypatch):
    project, fake = linear
    before = sorted(p for p in (project / "docs" / "product").rglob("*"))
    doc = {"epic": {"title": "Billing", "description": BODY, "status": "READY"},
           "specs": [{"ref": "a", "title": "API", "description": BODY, "status": "READY"},
                     {"ref": "b", "title": "UI", "description": BODY, "status": "READY", "depends_on": ["a"]}]}
    rc, out = remote(project, capsys, monkeypatch, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 0 and out["epic"]["url"]
    first, second = (s["id"] for s in out["specs"])

    rc, again = remote(project, capsys, monkeypatch, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 0 and [s["id"] for s in again["specs"]] == [first, second] and all(s["reused"] for s in again["specs"])

    assert remote(project, capsys, monkeypatch, "start", second)[0] == 5  # first is not DONE
    rc, started = remote(project, capsys, monkeypatch, "start", first)
    assert rc == 0 and started["item"]["status"] == "IN_PROGRESS" and started["epic"]["title"] == "Billing"

    rc, out = remote(project, capsys, monkeypatch, "comment", first, "--dedupe-key", "k", "--text-stdin", stdin="note")
    assert rc == 0 and out["comment"]["body"] == "note"
    remote(project, capsys, monkeypatch, "comment", first, "--dedupe-key", "k", "--text-stdin", stdin="note")
    assert [c["body"] for c in remote(project, capsys, monkeypatch, "context", first)[1]["comments"]] == ["note"]

    result = json.dumps({"status": "COMPLETED", "summary": "done"})
    rc, out = remote(project, capsys, monkeypatch, "finish", first, "--result-stdin", stdin=result)
    assert rc == 0 and out["status"] == "DONE"
    assert remote(project, capsys, monkeypatch, "start", second)[0] == 0
    assert sorted(p for p in (project / "docs" / "product").rglob("*")) == before


def test_partial_publish_failure_reports_what_exists_and_retry_completes(linear, capsys, monkeypatch):
    project, fake = linear
    real = fake.tool_save_issue
    calls = []

    def fail_second(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise AuthRejected("rejected")
        return real(**kwargs)

    fake.tool_save_issue = fail_second
    doc = json.dumps({"specs": [{"title": "One", "description": BODY}, {"title": "Two", "description": BODY}]})
    rc, out = remote(project, capsys, monkeypatch, "publish", "--json-stdin", stdin=doc)
    assert rc == 7 and [s["title"] for s in out["partial"]["specs"]] == ["One"]
    rc, out = remote(project, capsys, monkeypatch, "publish", "--json-stdin", stdin=doc)
    assert rc == 0 and [s["reused"] for s in out["specs"]] == [True, False]


@pytest.mark.parametrize("error, code", [(AuthRejected("x"), 7), (RateLimited("x"), 8), (TransportFailure("x"), 8)])
def test_provider_failures_have_their_own_exit_codes(linear, capsys, monkeypatch, error, code):
    project, fake = linear

    def boom(*args, **kwargs):
        raise error

    fake.call = boom
    rc, out = remote(project, capsys, monkeypatch, "get", "ENG-1")
    assert rc == code and out["ok"] is False


def test_invalid_input_never_opens_a_connection(project, capsys, monkeypatch):
    def forbidden():
        raise AssertionError("connected")

    args = commands.parse(["finish", "X", "--result-file", "missing.json"])
    assert commands.execute(args, forbidden) == InvalidInput.exit_code


def test_publish_rejects_a_spec_id_used_as_epic():
    backend = LinearWorkBackend(FakeLinear(), {"team": "Engineering"})
    created = publish(backend, {"specs": [{"title": "One", "description": "x"}]})["specs"][0]
    with pytest.raises(InvalidInput):
        publish(backend, {"epic": {"id": created["id"]}, "specs": [{"title": "Two", "description": "x"}]})
    assert WorkService(backend).backend.get(created["id"]).title == "One"


# --- provider copies ---

SKILLS = {
    "execute-work": [".claude/skills/execute-work/SKILL.md", ".agents/skills/execute-work/SKILL.md",
                     ".cursor/skills/execute-work/SKILL.md", ".opencode/command/execute-work.md"],
    "prepare-work": [".claude/skills/prepare-work/SKILL.md", ".agents/skills/prepare-work/SKILL.md",
                     ".cursor/skills/prepare-work/SKILL.md", ".opencode/command/prepare-work.md"],
}
CONTRACT = {
    "execute-work": [r"### 1\. Resolve scope once.*?(?=### 4\.)", r"### 6\. Persist minimal state.*?(?=### 7\.)"],
    "prepare-work": [r"10\. Write the content.*?(?=## Final response)"],
}


@pytest.fixture
def everything(tmp_path):
    installer.apply(installer.build_plan(ALL, tmp_path), tmp_path)
    return tmp_path


@pytest.mark.parametrize("skill", SKILLS)
def test_every_provider_has_the_same_work_contract(everything, skill):
    for pattern in CONTRACT[skill]:
        found = {re.search(pattern, (everything / rel).read_text(encoding="utf-8"), re.S).group(0)
                 for rel in SKILLS[skill]}
        assert len(found) == 1


def test_skills_use_the_shared_commands_and_no_markdown_bookkeeping(everything):
    for rel in SKILLS["execute-work"]:
        text = (everything / rel).read_text(encoding="utf-8")
        assert "./ae-sdlc work start <ID>" in text and "./ae-sdlc work finish <ID>" in text
        assert "status: DONE\n" not in text
        assert "**do not spawn**" in text  # the narrow exception lifts nothing else
    for rel in SKILLS["prepare-work"]:
        text = (everything / rel).read_text(encoding="utf-8")
        assert "./ae-sdlc work publish --json-file" in text
        assert "Persist artifacts by editing" not in text


def test_skill_commands_avoid_redirection(everything):
    for rel in [*SKILLS["execute-work"], *SKILLS["prepare-work"]]:
        for line in (everything / rel).read_text(encoding="utf-8").splitlines():
            if line.startswith("./ae-sdlc work"):
                assert not re.search(r"[<>|]\s*[./\w]", line.replace("<ID>", "").replace("<EPIC-ID>", "")), line


def test_sandboxes_do_not_restrict_network_hosts(everything):
    # Codex ignores an "allow" rule's sandbox bypass under a permission profile, so the profile opens the network.
    network = tomllib.loads((everything / ".codex/config.toml").read_text())["permissions"]["project-edit"]["network"]
    assert network["enabled"] is True
    assert "domains" not in network
    claude = json.loads((everything / ".claude/settings.json").read_text())
    assert "Bash(./ae-sdlc work:*)" in claude["permissions"]["allow"]
    cursor = json.loads((everything / ".cursor/cli.json").read_text())
    assert "Shell(./ae-sdlc work:*)" in cursor["permissions"]["allow"]
    assert not {"WebFetch(*)", "WebSearch(*)"} & set(cursor["permissions"]["deny"])
    assert json.loads((everything / ".cursor/sandbox.json").read_text()) == {"networkPolicy": {"default": "allow"}}


def test_generated_project_validates(everything):
    proc = subprocess.run([sys.executable, "-I", str(everything / "ae-sdlc"), "validate"], cwd=everything,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
    assert "WARNING" not in proc.stdout


# --- workflow over a remote backend ---


def test_workflow_delegates_before_credentials_are_removed(project, tmp_path):
    select_backend(project, "linear")
    stub = tmp_path / "stub"
    stub.write_text('#!/bin/sh\necho "$* key=$LINEAR_API_KEY"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    proc = subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), "--provider", "claude", "workflow", "nightly", "ENG-1"],
                          cwd=project, capture_output=True, text=True,
                          env={"AE_SDLC_BIN": str(stub), "PATH": os.environ["PATH"], "LINEAR_API_KEY": "secret"})
    assert proc.stdout.strip() == f"_work --root {project} workflow nightly ENG-1 --provider claude key=secret"


def test_remote_workflow_runs_agents_without_tracker_credentials(linear, capsys, monkeypatch):
    project, fake = linear
    doc = {"epic": {"title": "Billing", "description": BODY, "status": "READY"},
           "specs": [{"ref": "a", "title": "API", "description": BODY, "status": "READY"}]}
    rc, out = remote(project, capsys, monkeypatch, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 0
    epic_id = out["epic"]["id"]

    seen = []

    def fake_agent(skill, args, tail, schema, model, background=False):
        seen.append((args, tail, os.environ.get("LINEAR_API_KEY")))
        return 0, {"status": "COMPLETED", "summary": "shipped", "blockers": []}

    adapter = types.SimpleNamespace(run=fake_agent, check_cli=lambda: None)
    providers = types.SimpleNamespace(resolve=lambda name=None: "fake", load=lambda name: adapter, BACKGROUND_TAIL="runner_owns_status=true",
                                      remove_tracker_credentials_from_env=lambda: os.environ.pop("LINEAR_API_KEY", None))
    monkeypatch.setattr(backend_cli.importlib, "import_module", lambda name: providers)
    monkeypatch.setenv("LINEAR_API_KEY", "secret")
    assert cli.main(["_work", "--root", str(project), "workflow", "nightly", epic_id]) == 0
    assert len(seen) == 1 and seen[0][2] is None and BODY.strip().splitlines()[0] in seen[0][1]
    rc, out = remote(project, capsys, monkeypatch, "get", seen[0][0])
    assert out["item"]["status"] == "DONE"


def test_recover_reports_nothing_owed(project):
    rc, out = work(project, "recover")
    assert rc == 0 and out == {"ok": True, "recovered": [], "remaining": []}


def test_requeue_moves_a_blocked_spec_back_to_ready(project):
    write_spec(project, "SPEC-001", status="BLOCKED", extra="blocker: why")
    rc, out = work(project, "recover", "SPEC-001", "--requeue")
    assert rc == 0 and out["requeued"]["status"] == "READY" and status_of(project, "SPEC-001") == "READY"


def test_start_refuses_a_spec_with_an_owed_update(project):
    write_spec(project, "SPEC-001", status="IN_PROGRESS")
    pending = project / ".ae-sdlc-runtime" / "pending"
    pending.mkdir(parents=True)
    (pending / "SPEC-001.json").write_text(json.dumps({"work_id": "SPEC-001", "target_status": "DONE", "blocker": None,
                                                        "allowed_from": ["IN_PROGRESS"], "comments": []}))
    rc, out = work(project, "start", "SPEC-001")
    assert rc == 5 and "recover" in out["error"]["message"]
    rc, out = work(project, "recover", "SPEC-001")
    assert rc == 0 and out["recovered"][0]["id"] == "SPEC-001" and status_of(project, "SPEC-001") == "DONE"
