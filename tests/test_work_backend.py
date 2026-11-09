"""PR 1: WorkBackend contract, local adapter, WorkService lifecycle, and backend selection."""
from __future__ import annotations

import subprocess
import sys
import types

import pytest

from conftest import run_entry, write_spec


@pytest.fixture
def work(project, load_store):
    from work import backend, config, models, service
    from work.backends import local
    b = local.LocalWorkBackend(load_store.WorkStore(project))
    return types.SimpleNamespace(backend=b, service=service.WorkService(b), errors=backend, models=models,
                                 config=config, contract=backend.WorkBackend)


def test_local_backend_satisfies_protocol(work):
    assert isinstance(work.backend, work.contract)


def test_item_normalization(project, work):
    write_spec(project, "EPIC-001", kind="epic", title="E")
    write_spec(project, "SPEC-002", epic="EPIC-001", deps=["SPEC-001"], title="S", status="BLOCKED",
               extra="blocker: why")
    work.backend.store.scan_once()
    s = work.backend.get("SPEC-002")
    assert (s.id, s.kind, s.title, s.status, s.parent_id, s.dependencies, s.blocker) == (
        "SPEC-002", "spec", "S", "BLOCKED", "EPIC-001", ["SPEC-001"], "why")
    assert s.description == "\n# Body\n\nText.\n" and s.revision  # body is kept verbatim


def test_standalone_and_epic_scope_resolution(project, work):
    write_spec(project, "SPEC-001")
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-003", epic="EPIC-001")
    write_spec(project, "SPEC-002", epic="EPIC-001")
    work.backend.store.scan_once()
    assert [s.id for s in work.backend.list_specs("SPEC-001")] == ["SPEC-001"]
    assert [s.id for s in work.backend.list_specs("EPIC-001")] == ["SPEC-002", "SPEC-003"]
    assert work.backend.list_specs("NOPE") == []


def test_dependencies_enforced(project, work):
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-001", epic="EPIC-001")
    write_spec(project, "SPEC-002", epic="EPIC-001", deps=["SPEC-001"])
    write_spec(project, "SPEC-003", epic="EPIC-001", deps=["SPEC-404"])
    work.backend.store.scan_once()
    assert [s.id for s in work.service.eligible_specs("EPIC-001")] == ["SPEC-001"]
    work.backend.transition("SPEC-001", "DONE")
    assert [s.id for s in work.service.eligible_specs("EPIC-001")] == ["SPEC-002"]


def test_epic_is_never_eligible(project, work):
    write_spec(project, "EPIC-001", kind="epic", status="READY")
    work.backend.store.scan_once()
    assert work.service.eligible_specs("EPIC-001") == []


def test_lifecycle_ready_in_progress_done(project, work, load_store):
    write_spec(project, "SPEC-001")
    work.backend.store.scan_once()
    assert work.service.start_execution("SPEC-001").status == "IN_PROGRESS"
    assert load_store.WorkStore(project).get("SPEC-001").status == "IN_PROGRESS"
    f = work.service.finish_execution("SPEC-001", 0, {"status": "COMPLETED", "summary": "ok"}, "x")
    assert (f.disposition, f.detail) == ("completed", "ok")
    assert load_store.WorkStore(project).get("SPEC-001").status == "DONE"


@pytest.mark.parametrize("rc,result,disposition,detail", [
    (0, {"status": "NEEDS_INPUT", "blockers": ["pick db"]}, "blocked", "pick db"),
    (0, {"status": "BLOCKED"}, "blocked", "needs input"),
    (0, {"status": "FAILED", "summary": "boom"}, "failed", "Execution failed: boom"),
    (0, {"status": "PARTIAL"}, "failed", "Execution failed: fallback"),
    (3, {"status": "COMPLETED"}, "failed", "Execution failed: fallback"),
])
def test_non_completed_outcomes_become_blocked(project, work, rc, result, disposition, detail):
    write_spec(project, "SPEC-001")
    work.backend.store.scan_once()
    f = work.service.finish_execution("SPEC-001", rc, result, "fallback")
    assert (f.disposition, f.detail) == (disposition, detail)
    item = work.backend.get("SPEC-001")
    assert (item.status, item.blocker) == ("BLOCKED", detail)


def test_blocked_work_is_not_eligible_and_blocker_clears(project, work):
    write_spec(project, "SPEC-001", status="BLOCKED", extra="blocker: why")
    work.backend.store.scan_once()
    assert work.service.eligible_specs("SPEC-001") == []
    assert work.backend.transition("SPEC-001", "READY").blocker is None


def test_unknown_ids_and_statuses_rejected(project, work):
    write_spec(project, "SPEC-001")
    work.backend.store.scan_once()
    with pytest.raises(work.errors.WorkItemNotFound):
        work.backend.get("NOPE")
    with pytest.raises(work.errors.WorkItemNotFound):
        work.backend.transition("NOPE", "DONE")
    with pytest.raises(work.errors.InvalidTransition):
        work.backend.transition("SPEC-001", "SHIPPED")


def test_any_valid_transition_stays_permitted(project, work):
    """Decision D1: the local backend keeps no transition table."""
    write_spec(project, "SPEC-001", status="DONE")
    work.backend.store.scan_once()
    assert work.backend.transition("SPEC-001", "DRAFT").status == "DRAFT"


def test_create_uses_documented_layout(project, work, load_store):
    m = work.models
    work.backend.create(m.WorkItem("EPIC-001", "epic", "Big Feature", "# Epic\n", "DRAFT"))
    work.backend.create(m.WorkItem("SPEC-001", "spec", "Part A", "# A\n", "READY", parent_id="EPIC-001",
                                   dependencies=["SPEC-009"]))
    work.backend.create(m.WorkItem("SPEC-010", "spec", "Solo", "# S\n", "DRAFT"))
    product = project / "docs/product"
    assert (product / "epics/EPIC-001-big-feature/epic.md").exists()
    assert (product / "epics/EPIC-001-big-feature/specs/SPEC-001-part-a.md").exists()
    assert (product / "standalone/SPEC-010-solo.md").exists()
    rec = load_store.WorkStore(project).get("SPEC-001")
    assert rec.meta["epic"] == "EPIC-001" and rec.meta["dependencies"] == ["SPEC-009"]
    assert rec.meta["source_of_truth"] == "repository"


def test_create_rejects_existing_id(project, work):
    write_spec(project, "SPEC-001")
    work.backend.store.scan_once()
    with pytest.raises(work.errors.WorkItemExists):
        work.backend.create(work.models.WorkItem("SPEC-001", "spec", "X", "", "DRAFT"))


def test_update_preserves_status_and_unknown_keys_and_checks_revision(project, work):
    p = write_spec(project, "SPEC-001", extra="owner: me")
    work.backend.store.scan_once()
    item = work.backend.get("SPEC-001")
    item.title, item.description, item.status = "New", "# New\n", "DONE"
    with pytest.raises(work.errors.RevisionConflict):
        work.backend.update(item, expected_revision="stale")
    updated = work.backend.update(item, expected_revision=item.revision)
    assert updated.title == "New" and updated.status == "READY"
    assert "owner: me" in p.read_text() and "# New" in p.read_text()


def test_transition_output_matches_work_store_rendering(project, work, load_store):
    a = write_spec(project, "SPEC-001", extra="owner: me")
    original = a.read_text()
    work.backend.store.scan_once()
    work.backend.transition("SPEC-001", "IN_PROGRESS")
    via_backend = a.read_text()
    a.write_text(original)
    load_store.WorkStore(project).set_status("SPEC-001", "IN_PROGRESS")
    assert a.read_text() == via_backend


def test_comments_are_explicitly_unsupported_locally(project, work):
    write_spec(project, "SPEC-001")
    work.backend.store.scan_once()
    assert work.backend.get_comments("SPEC-001") == []
    with pytest.raises(work.errors.CapabilityMissing):
        work.backend.add_comment("SPEC-001", "hello")


def test_prepare_execution_context(project, work):
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-001", epic="EPIC-001", status="DONE")
    write_spec(project, "SPEC-002", epic="EPIC-001", deps=["SPEC-001"])
    work.backend.store.scan_once()
    ctx = work.service.prepare_execution_context("SPEC-002")
    assert ctx.parent.id == "EPIC-001" and [d.id for d in ctx.dependencies] == ["SPEC-001"]


@pytest.mark.parametrize("scope,only", [(None, None), ("EPIC-001", None), ("SPEC-001", None), ("NOPE", None),
                                        (None, "READY"), (None, "BLOCKED"), ("EPIC-001", "DONE")])
def test_status_lines_match_legacy_summary(project, work, load_store, scope, only):
    write_spec(project, "EPIC-001", kind="epic", status="IN_PROGRESS", title="E")
    write_spec(project, "SPEC-001", epic="EPIC-001", title="A")
    write_spec(project, "SPEC-002", epic="EPIC-001", deps=["SPEC-001"], status="BLOCKED", extra="blocker: why")
    write_spec(project, "SPEC-003", status="DONE")
    store = load_store.WorkStore(project)
    work.backend.store.scan_once()
    assert work.service.status_lines(scope, only) == store.summary_lines(scope, only_status=only)


# --- backend selection ---

def test_missing_config_defaults_to_local(project, work):
    assert work.config.backend_name(project) == "local"
    assert type(work.config.load_backend(project)).__name__ == "LocalWorkBackend"


def test_config_selects_local_and_defaults_when_key_absent(project, work):
    cfg = project / ".ae-sdlc/work.toml"
    cfg.write_text('[work]\nbackend = "local"\n')
    assert work.config.backend_name(project) == "local"
    cfg.write_text("[work.context]\nmax_comments_per_item = 5\n")
    assert work.config.backend_name(project) == "local"


@pytest.mark.parametrize("text", ['[work]\nbackend = "linear"\n', "not toml [", "[work]\nbackend = 3\n"])
def test_bad_or_unavailable_config_is_a_clean_error(project, work, text):
    (project / ".ae-sdlc/work.toml").write_text(text)
    with pytest.raises(work.errors.WorkConfigError):
        work.config.load_backend(project)
    p = run_entry(project, "status")
    assert p.returncode != 0 and "Traceback" not in p.stderr and "work.toml" not in p.stdout


def test_installer_does_not_create_work_toml(project):
    assert not (project / ".ae-sdlc/work.toml").exists()


# --- injection and boundaries ---

class FakeBackend:
    """In-memory backend: proves the runner depends only on the contract."""

    def __init__(self, models, items):
        self.models, self.items = models, {i.id: i for i in items}

    def get(self, work_id):
        return self.items[work_id]

    def list_specs(self, scope, status=None):
        return [i for i in self.items.values() if i.kind == "spec" and (status is None or i.status == status)]

    def get_dependencies(self, work_id):
        return []

    def get_related(self, work_id):
        return []

    def get_comments(self, work_id, limit=None):
        return []

    def add_comment(self, work_id, text, dedupe_key=None):
        return None

    def transition(self, work_id, target_status, blocker=None):
        self.items[work_id].status = str(target_status)
        return self.items[work_id]


def test_runner_accepts_an_injected_backend(project, load_store, monkeypatch):
    import providers
    import run_workflow
    from work import models
    monkeypatch.setattr(run_workflow, "ROOT", project)
    monkeypatch.setattr(run_workflow, "RUNTIME", project / ".ae-sdlc-runtime/runs")
    adapter = types.SimpleNamespace(check_cli=lambda: None,
                                    run=lambda *a, **k: (0, {"status": "COMPLETED", "summary": "ok"}))
    monkeypatch.setattr(providers, "resolve", lambda n=None: "fake")
    monkeypatch.setattr(providers, "load", lambda n: adapter)
    fake = FakeBackend(models, [models.WorkItem("ENG-1", "spec", "T", "", "READY")])
    assert run_workflow.main(["nightly", "ENG-1"], backend=fake) == 0
    assert fake.items["ENG-1"].status == "DONE"


def test_entrypoint_loads_local_backend_with_no_installed_package(project):
    """-I isolates from site-packages; the real entrypoint must still load the local backend."""
    write_spec(project, "SPEC-001", title="Thing")
    p = run_entry(project, "status", "--ready")
    assert p.returncode == 0 and p.stdout.splitlines() == ["READY", "  SPEC-001 [spec] Thing"]


def test_package_exposes_the_same_files_as_the_generated_runtime():
    from ae_sdlc import installer
    code = ("import ae_sdlc.work.service as s, ae_sdlc.work.backends.local as l;"
            "print(s.__file__); print(l.__file__)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={"PYTHONPATH": str(installer.DATA.parents[1])})
    assert out.returncode == 0, out.stderr
    base = installer.DATA / "core/scripts/ae-sdlc/work"
    assert out.stdout.split() == [str(base / "service.py"), str(base / "backends/local.py")]
