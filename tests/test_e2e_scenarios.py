"""PR 7: the release scenarios, run the same way over the local, Linear and Jira backends.

Each test name starts with the scenario number from docs/Implementation_Plan.md. Trackers are the
stateful fakes from the backend tests and the coding agent is scripted, so nothing here needs a
network or credentials. tests/test_e2e_live.py runs the smoke version against real trackers.
"""
from __future__ import annotations

import copy
import io
import json
import os
import stat
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_jira_backend import SETTINGS, FakeJira
from test_linear_backend import FakeLinear
from test_work_commands import BODY, work as local_work

from ae_sdlc import backend_cli, cli, installer
from ae_sdlc.work.config import read_config, set_backend, write_config
from ae_sdlc.work.mcp.errors import AuthRejected, RateLimited, TransportFailure

ALL = ["local", "linear", "jira"]
REMOTE = ["linear", "jira"]
OUTAGES = [(AuthRejected("rejected"), 7), (RateLimited("slow down"), 8), (TransportFailure("timed out"), 8)]
COMPLETED = (0, {"status": "COMPLETED", "summary": "shipped", "blockers": []})
CREATE_TOOL = {"linear": "save_issue", "jira": "createJiraIssue"}
TRANSITION_TOOL = {"linear": "save_issue", "jira": "transitionJiraIssue"}


def spec_doc(*specs: dict, epic: bool = True) -> dict:
    doc: dict = {"specs": [{"description": BODY, "status": "READY", **s} for s in specs]}
    if epic:
        doc["epic"] = {"title": "Billing", "description": BODY, "status": "READY"}
    return doc


class Rig:
    """Drives one backend through the commands a user or a skill would run."""

    def __init__(self, name, project, fake, capsys, monkeypatch):
        self.name, self.project, self.fake, self.capsys, self.monkeypatch = name, project, fake, capsys, monkeypatch
        self.agent_calls: list[str] = []
        self.prompts: dict[str, str] = {}
        self.results: dict[str, tuple] = {}
        self.during_agent: dict[str, object] = {}
        self.outage: Exception | None = None

    def agent(self, skill, args, tail, schema, model, background=False):
        self.agent_calls.append(args)
        self.prompts[args] = tail
        if args in self.during_agent:
            self.during_agent.pop(args)()
        return self.results.get(args, COMPLETED)

    def work(self, *args: str, stdin: str = "") -> tuple[int, dict]:
        if self.name == "local":
            return local_work(self.project, *args, stdin=stdin)
        self.capsys.readouterr()
        self.monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        rc = cli.main(["_work", "--root", str(self.project), "work", *args])
        return rc, json.loads(self.capsys.readouterr().out)

    def publish(self, doc: dict) -> dict:
        rc, out = self.work("publish", "--json-stdin", stdin=json.dumps(doc))
        assert rc == 0, out
        return out

    def get(self, work_id: str) -> dict:
        rc, out = self.work("get", work_id)
        assert rc == 0, out
        return out["item"]

    def status(self, work_id: str) -> str:
        return self.get(work_id)["status"]

    def comments(self, work_id: str) -> list[str]:
        return [c["body"] for c in self.work("context", work_id)[1]["comments"]]

    def workflow(self, scope: str):
        """Return the runner's exit code, or the message when it refuses to start."""
        try:
            if self.name == "local":
                import run_workflow
                return run_workflow.main(["nightly", scope])
            return cli.main(["_work", "--root", str(self.project), "workflow", "nightly", scope])
        except SystemExit as exc:
            return exc.code

    def report(self) -> dict:
        runs = (self.project / ".ae-sdlc-runtime" / "runs").glob("*/report.json")
        return json.loads(max(runs, key=lambda r: r.stat().st_mtime_ns).read_text(encoding="utf-8"))

    def stored_specs(self) -> int:
        if self.name == "local":
            return len(list((self.project / "docs" / "product").rglob("SPEC-*.md")))
        items = self.fake.issues.values()
        return sum(1 for i in items if i.get("type") != "Epic")

    def status_writes(self) -> int:
        if self.name == "linear":
            return sum(1 for a in self.fake.writes("save_issue") if "state" in a)
        return len(self.fake.used("transitionJiraIssue"))


@pytest.fixture(params=ALL)
def rig(request, project, load_store, capsys, monkeypatch):
    return _rig(request.param, project, capsys, monkeypatch)


@pytest.fixture(params=REMOTE)
def remote(request, project, load_store, capsys, monkeypatch):
    return _rig(request.param, project, capsys, monkeypatch)


def _rig(name, project, capsys, monkeypatch) -> Rig:
    fake = {"local": lambda: None, "linear": FakeLinear, "jira": FakeJira}[name]()
    rig = Rig(name, project, fake, capsys, monkeypatch)
    adapter = types.SimpleNamespace(run=rig.agent, check_cli=lambda: None)
    if name == "local":
        import providers
        import run_workflow
        monkeypatch.setattr(run_workflow, "ROOT", project)
        monkeypatch.setattr(run_workflow, "RUNTIME", project / ".ae-sdlc-runtime" / "runs")
        monkeypatch.setattr(providers, "resolve", lambda n=None: "fake")
        monkeypatch.setattr(providers, "load", lambda n: adapter)
        return rig

    config = read_config(project)
    config["work"] = {"backend": name}
    config[name] = {"team": "AND"} if name == "linear" else SETTINGS
    write_config(project, config)
    real_call = fake.call

    def call(tool, arguments, *, retry_safe=False):
        if rig.outage:
            raise rig.outage
        return real_call(tool, arguments, retry_safe=retry_safe)

    fake.call = call

    @contextmanager
    def client(url, credential):
        yield fake

    stub = types.SimpleNamespace(resolve=lambda n=None: "fake", load=lambda n: adapter, BACKGROUND_TAIL="runner_owns_status=true",
                                 remove_tracker_credentials_from_env=lambda: None)
    monkeypatch.setattr(backend_cli, "McpClient", client)
    monkeypatch.setattr(backend_cli.auth, "resolve", lambda provider: object())
    monkeypatch.setattr(backend_cli.importlib, "import_module", lambda module: stub)
    return rig


def test_01_to_03_prepare_creates_an_epic_with_a_child_spec(rig):
    out = rig.publish(spec_doc({"ref": "a", "title": "API"}))
    epic_id, spec_id = out["epic"]["id"], out["specs"][0]["id"]
    assert rig.get(rig.get(spec_id)["parent_id"])["id"] == epic_id
    assert BODY.strip().splitlines()[-1] in rig.get(spec_id)["description"]
    if rig.name == "linear":
        assert [p["name"] for p in rig.fake.projects.values()] == ["Billing"]
        assert rig.fake.issues[spec_id]["projectId"] == next(iter(rig.fake.projects.values()))["uuid"]
    elif rig.name == "jira":
        assert rig.fake.issues[epic_id]["type"] == "Epic" and rig.fake.issues[spec_id]["parent"] == epic_id
    else:
        assert (rig.project / out["specs"][0]["path"]).exists()


def test_04_standalone_spec(rig):
    out = rig.publish(spec_doc({"title": "Solo"}, epic=False))
    assert out["epic"] is None and not rig.get(out["specs"][0]["id"])["parent_id"]


def test_05_duplicate_prepare_reuses_and_updates_existing_work(rig):
    doc = spec_doc({"title": "API"}, epic=False)
    first = rig.publish(doc)["specs"][0]["id"]
    assert rig.publish(doc)["specs"][0]["id"] == first and rig.stored_specs() == 1
    rig.publish({"specs": [{"id": first, "description": "## Goal\n\nRevised.\n"}]})
    assert "Revised." in rig.get(first)["description"] and rig.stored_specs() == 1


def test_06_ready_spec_runs_and_becomes_done(rig):
    out = rig.publish(spec_doc({"title": "API"}))
    spec_id = out["specs"][0]["id"]
    assert rig.workflow(out["epic"]["id"]) == 0
    assert rig.agent_calls == [spec_id] and rig.status(spec_id) == "DONE"
    report = rig.report()
    assert [e["id"] for e in report["completed"]] == [spec_id] and report["remote_edits"] == []


def test_07_draft_work_cannot_execute(rig):
    spec_id = rig.publish(spec_doc({"title": "API", "status": "DRAFT"}, epic=False))["specs"][0]["id"]
    assert rig.work("start", spec_id)[0] == 5
    assert rig.workflow(spec_id) == 0 and rig.agent_calls == [] and rig.status(spec_id) == "DRAFT"


def test_08_and_12_blocker_is_persisted_and_holds_until_resolved(rig):
    spec_id = rig.publish(spec_doc({"title": "API"}, epic=False))["specs"][0]["id"]
    rig.results[spec_id] = (0, {"status": "NEEDS_INPUT", "summary": "", "blockers": ["pick a database"]})
    assert rig.workflow(spec_id) == 0
    item = rig.get(spec_id)
    assert item["status"] == "BLOCKED" and item["blocker"] == "pick a database"

    rc, out = rig.work("start", spec_id)
    assert rc == 5 and "pick a database" in out["error"]["message"]
    rig.workflow(spec_id)
    assert rig.agent_calls == [spec_id]

    del rig.results[spec_id]
    assert rig.work("recover", spec_id, "--requeue")[0] == 0
    assert rig.workflow(spec_id) == 0 and rig.status(spec_id) == "DONE" and rig.agent_calls == [spec_id, spec_id]


def test_09_unsatisfied_dependency_prevents_execution(rig):
    out = rig.publish(spec_doc({"ref": "a", "title": "API", "status": "DRAFT"}, {"ref": "b", "title": "UI", "depends_on": ["a"]}))
    second = out["specs"][1]["id"]
    assert rig.work("start", second)[0] == 5
    assert rig.workflow(out["epic"]["id"]) == 0 and rig.agent_calls == [] and rig.status(second) == "READY"


def test_10_execution_consumes_comments(remote):
    spec_id = remote.publish(spec_doc({"title": "API"}, epic=False))["specs"][0]["id"]
    remote.work("comment", spec_id, "--text-stdin", stdin="Use Postgres, decided on Monday")
    assert remote.workflow(spec_id) == 0
    assert "Use Postgres, decided on Monday" in remote.prompts[spec_id] and "untrusted" in remote.prompts[spec_id]


def test_11_agent_comments_on_a_linked_item_once(remote):
    out = remote.publish(spec_doc({"ref": "a", "title": "API"}, {"ref": "b", "title": "UI", "depends_on": ["a"]}))
    first, second = (s["id"] for s in out["specs"])
    finding = "The API now pages results; the client must follow the cursor"
    remote.results[second] = (0, {"status": "COMPLETED", "summary": "shipped", "blockers": [], "comments": [
        {"work_item_id": first, "text": finding}, {"work_item_id": first, "text": finding},
        {"work_item_id": "OTHER-1", "text": "out of scope"}]})
    assert remote.workflow(out["epic"]["id"]) == 0
    assert remote.comments(first).count(finding) == 1
    dropped = remote.report()["completed"][1]["dropped_comments"]
    assert [d["work_id"] for d in dropped] == ["OTHER-1"]


@pytest.mark.parametrize("failure, code", OUTAGES, ids=["auth", "rate-limit", "timeout"])
def test_13_and_14_tracker_failure_before_start_runs_no_agent(remote, failure, code):
    spec_id = remote.publish(spec_doc({"title": "API"}, epic=False))["specs"][0]["id"]
    remote.outage = failure
    assert remote.work("get", spec_id)[0] == code
    assert remote.workflow(spec_id) not in (0, None)
    remote.outage = None
    assert remote.agent_calls == [] and remote.status(spec_id) == "READY"


@pytest.mark.parametrize("failure, code", OUTAGES, ids=["auth", "rate-limit", "timeout"])
def test_13_and_14_tracker_failure_after_the_agent_is_recovered_without_a_rerun(remote, failure, code):
    spec_id = remote.publish(spec_doc({"title": "API"}, epic=False))["specs"][0]["id"]

    def lose_tracker():
        remote.outage = failure

    remote.during_agent[spec_id] = lose_tracker
    assert remote.workflow(spec_id) == 1
    assert [u["id"] for u in remote.report()["pending_updates"]] == [spec_id]
    rc, out = remote.work("recover")
    assert rc == 0 and out["remaining"][0]["id"] == spec_id

    remote.outage = None
    assert remote.status(spec_id) == "IN_PROGRESS"
    assert remote.workflow(spec_id) == 1 and remote.agent_calls == [spec_id]
    assert remote.work("recover")[1]["recovered"][0]["status"] == "DONE"
    assert remote.status(spec_id) == "DONE" and remote.agent_calls == [spec_id]


def test_15_lost_create_response_does_not_duplicate(remote):
    remote.fake.lose_response_of.add(CREATE_TOOL[remote.name])
    out = remote.publish(spec_doc({"title": "API"}, epic=False))
    assert remote.stored_specs() == 1 and out["specs"][0]["id"] in remote.fake.issues


def test_16_lost_status_response_is_reconciled(remote):
    spec_id = remote.publish(spec_doc({"title": "API"}, epic=False))["specs"][0]["id"]
    tool = TRANSITION_TOOL[remote.name]
    remote.during_agent[spec_id] = lambda: remote.fake.lose_response_of.add(tool)
    assert remote.workflow(spec_id) == 1
    writes = remote.status_writes()
    assert remote.work("recover")[1]["remaining"] == []
    assert remote.status(spec_id) == "DONE" and remote.agent_calls == [spec_id]
    assert remote.status_writes() == writes, "the status had landed, so recovery must not write it again"
    assert remote.comments(spec_id).count("**Completed:** shipped") == 1


def test_17_edit_during_execution_is_reported(rig):
    spec_id = rig.publish(spec_doc({"title": "API"}, epic=False))["specs"][0]["id"]
    rig.during_agent[spec_id] = lambda: rig.publish({"specs": [{"id": spec_id, "description": "## Goal\n\nChanged mid-run.\n"}]})
    assert rig.workflow(spec_id) == 0
    report = rig.report()
    assert [e["id"] for e in report["remote_edits"]] == [spec_id]
    assert report["remote_edits"][0]["claimed_revision"] != report["remote_edits"][0]["current_revision"]
    assert rig.status(spec_id) == "DONE"


def test_18_epic_completes_only_when_every_spec_is_done(rig):
    out = rig.publish(spec_doc({"ref": "a", "title": "API"}, {"ref": "b", "title": "UI"}))
    epic_id, first, second = out["epic"]["id"], out["specs"][0]["id"], out["specs"][1]["id"]
    rig.results[second] = (0, {"status": "BLOCKED", "summary": "", "blockers": ["needs design"]})
    assert rig.workflow(epic_id) == 0
    assert rig.status(first) == "DONE" and rig.status(second) == "BLOCKED" and rig.status(epic_id) != "DONE"

    del rig.results[second]
    rig.work("recover", second, "--requeue")
    assert rig.workflow(epic_id) == 0 and rig.status(epic_id) == "DONE"


def _entrypoint(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), *args], cwd=project, capture_output=True, text=True,
                          encoding="utf-8")


def test_19_switching_backend_neither_migrates_nor_deletes(remote):
    project = remote.project
    spec_id = remote.publish(spec_doc({"title": "Remote"}, epic=False))["specs"][0]["id"]
    tracker = copy.deepcopy(remote.fake.issues)
    pending = project / ".ae-sdlc-runtime" / "pending"
    pending.mkdir(parents=True)
    owed = {"work_id": spec_id, "target_status": "DONE", "blocker": None, "allowed_from": ["IN_PROGRESS"], "comments": []}
    (pending / f"{spec_id}.json").write_text(json.dumps(owed), encoding="utf-8")
    calls_before = len(remote.fake.calls)
    settings_before = read_config(project)[remote.name]

    assert "Active backend: local" in _entrypoint(project, "backend", "use", "local").stdout
    assert read_config(project)[remote.name] == settings_before
    assert "No matching durable work" in _entrypoint(project, "status").stdout
    doc = spec_doc({"title": "Local"}, epic=False)
    rc, out = local_work(project, "publish", "--json-stdin", stdin=json.dumps(doc))
    assert rc == 0
    local_path = project / out["specs"][0]["path"]
    rc, out = local_work(project, "recover")
    assert out["recovered"] == [] and out["remaining"][0]["id"] == spec_id, "an update owed to another backend is kept"
    assert (pending / f"{spec_id}.json").exists()
    assert len(remote.fake.calls) == calls_before and remote.fake.issues == tracker

    set_backend(project, remote.name)
    assert remote.get(spec_id)["title"] == "Remote" and remote.stored_specs() == 1
    assert local_path.exists() and remote.fake.issues.keys() == tracker.keys()


AGENT_STUBS = {
    "claude": ("claude", 'printf \'{"is_error": false, "structured_output": %s}\' "$RESULT"'),
    "codex": ("codex", 'while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then printf %s "$RESULT" > "$2"; fi; shift; done'),
    "cursor": ("agent", 'printf %s "$RESULT"'),
    "opencode": ("opencode", 'printf \'{"type":"text","part":{"type":"text","text":%s}}\\n\' "$(printf %s "$RESULT" | sed \'s/"/\\\\"/g; s/^/"/; s/$/"/\')"'),
    "agy": ("agy", 'printf \'{"status": "OK", "structured_output": %s}\' "$RESULT"'),
}


@pytest.mark.parametrize("provider", installer.PROVIDERS)
def test_20_every_installed_provider_runs_the_same_workflow(tmp_path, provider):
    project = tmp_path / "project"
    project.mkdir()
    installer.apply(installer.build_plan(list(installer.PROVIDERS), project), project)
    rc, out = local_work(project, "publish", "--json-stdin", stdin=json.dumps(spec_doc({"title": "API"}, epic=False)))
    assert rc == 0
    spec_id = out["specs"][0]["id"]

    stubs = tmp_path / "bin"
    stubs.mkdir()
    binary, body = AGENT_STUBS[provider]
    script = stubs / binary
    script.write_text(f'#!/bin/sh\nRESULT=\'{{"status": "COMPLETED", "summary": "shipped", "blockers": []}}\'\n{body}\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), "--provider", provider, "workflow", "nightly", spec_id],
                          cwd=project, capture_output=True, text=True, encoding="utf-8", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert local_work(project, "get", spec_id)[1]["item"]["status"] == "DONE"


def test_tracker_traffic_stays_within_budget(remote):
    """The evaluation metrics that do not need a real tracker: duplicates, retries and comment repeats."""
    out = remote.publish(spec_doc({"title": "API"}, epic=False))
    spec_id = out["specs"][0]["id"]
    before, writes_before = len(remote.fake.calls), remote.status_writes()
    assert remote.workflow(spec_id) == 0
    assert remote.stored_specs() == 1 and remote.agent_calls == [spec_id]
    assert remote.status_writes() - writes_before == 2, "one move to IN_PROGRESS and one to DONE"
    assert remote.comments(spec_id) == ["**Completed:** shipped"]
    assert len(remote.fake.calls) - before <= 40, "tracker calls per executed spec"
