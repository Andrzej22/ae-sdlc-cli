"""Characterization tests for the local WorkStore (PR 0 baseline).

These pin CURRENT behavior, including quirks, so PR 1 can refactor behind a WorkBackend
without silently changing semantics. Quirks are marked QUIRK; changing them is a deliberate decision.
"""
from __future__ import annotations

import pytest

from conftest import write_spec


def test_scan_reads_epics_and_specs_skips_templates_and_readme(project, load_store):
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-001", epic="EPIC-001")
    write_spec(project, "SPEC-002")  # standalone
    s = load_store.WorkStore(project)
    assert sorted(s.by_id) == ["EPIC-001", "SPEC-001", "SPEC-002"]
    assert s.get("EPIC-001").type == "epic"
    assert s.get("SPEC-002").meta["epic"] is None  # empty value -> None


def test_files_without_frontmatter_or_other_types_ignored(project, load_store):
    d = project / "docs" / "product" / "standalone"
    (d / "notes.md").write_text("no frontmatter\n")
    (d / "intent.md").write_text("---\nartifact_type: intent\nwork_item_id: INT-1\n---\nx\n")
    assert load_store.WorkStore(project).by_id == {}


def test_epic_scope_returns_only_its_specs_sorted(project, load_store):
    write_spec(project, "EPIC-001", kind="epic")
    write_spec(project, "SPEC-002", epic="EPIC-001")
    write_spec(project, "SPEC-001", epic="EPIC-001")
    write_spec(project, "SPEC-003")  # other
    s = load_store.WorkStore(project)
    assert [r.id for r in s.specs_for_scope("EPIC-001")] == ["SPEC-001", "SPEC-002"]


def test_spec_scope_and_status_filter(project, load_store):
    write_spec(project, "SPEC-001", status="DRAFT")
    s = load_store.WorkStore(project)
    assert [r.id for r in s.specs_for_scope("SPEC-001")] == ["SPEC-001"]
    assert s.specs_for_scope("SPEC-001", status="READY") == []


def test_scope_may_be_a_relative_path(project, load_store):
    p = write_spec(project, "SPEC-001")
    s = load_store.WorkStore(project)
    # a path is not a key in by_id, so this exercises the file-path fallback
    rel = str(p.relative_to(project))
    assert [r.id for r in s.specs_for_scope(rel)] == ["SPEC-001"]


def test_unknown_scope_yields_nothing(project, load_store):
    assert load_store.WorkStore(project).specs_for_scope("SPEC-999") == []


def test_epic_is_not_executable(project, load_store):
    write_spec(project, "EPIC-001", kind="epic", status="READY")
    assert load_store.WorkStore(project).eligible_specs("EPIC-001") == []


def test_dependencies_must_all_be_done(project, load_store):
    write_spec(project, "SPEC-001", status="DONE")
    write_spec(project, "SPEC-002", status="IN_PROGRESS")
    write_spec(project, "SPEC-003", deps=["SPEC-001"])
    write_spec(project, "SPEC-004", deps=["SPEC-001", "SPEC-002"])
    s = load_store.WorkStore(project)
    assert [r.id for r in s.eligible_specs("SPEC-003")] == ["SPEC-003"]
    assert s.eligible_specs("SPEC-004") == []


def test_unknown_dependency_blocks_eligibility(project, load_store):
    write_spec(project, "SPEC-001", deps=["SPEC-404"])
    assert load_store.WorkStore(project).eligible_specs("SPEC-001") == []


def test_only_ready_is_eligible_by_default(project, load_store):
    for i, st in enumerate(["DRAFT", "BLOCKED", "IN_PROGRESS", "DONE"], 1):
        write_spec(project, f"SPEC-00{i}", status=st)
        assert load_store.WorkStore(project).eligible_specs(f"SPEC-00{i}") == []


def test_lifecycle_ready_in_progress_done_persists_to_disk(project, load_store):
    p = write_spec(project, "SPEC-001")
    s = load_store.WorkStore(project)
    s.set_status("SPEC-001", "IN_PROGRESS")
    s.set_status("SPEC-001", "DONE")
    assert "status: DONE" in p.read_text()
    assert load_store.WorkStore(project).get("SPEC-001").status == "DONE"


def test_blocked_records_blocker_and_done_clears_it(project, load_store):
    p = write_spec(project, "SPEC-001")
    s = load_store.WorkStore(project)
    s.set_status("SPEC-001", "BLOCKED", blocker="needs\ninput ")
    assert "blocker: needs input" in p.read_text()  # newlines flattened
    s.set_status("SPEC-001", "READY")
    assert "blocker" not in p.read_text()


def test_unknown_id_and_invalid_status_rejected(project, load_store):
    write_spec(project, "SPEC-001")
    s = load_store.WorkStore(project)
    with pytest.raises(KeyError):
        s.set_status("SPEC-404", "DONE")
    with pytest.raises(ValueError):
        s.set_status("SPEC-001", "WIP")


def test_QUIRK_no_transition_validation_any_to_any(project, load_store):
    """QUIRK: DONE -> DRAFT, DRAFT -> DONE are accepted. PR 1 must decide whether to keep this."""
    write_spec(project, "SPEC-001", status="DONE")
    s = load_store.WorkStore(project)
    s.set_status("SPEC-001", "DRAFT")
    s.set_status("SPEC-001", "DONE")
    assert s.get("SPEC-001").status == "DONE"


def test_set_status_preserves_body_and_unknown_frontmatter(project, load_store):
    body = "# Body\n\n```py\nx = {'a': 1}\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    p = write_spec(project, "SPEC-001", body=body, extra="custom_key: keep-me")
    load_store.WorkStore(project).set_status("SPEC-001", "DONE")
    text = p.read_text()
    assert body in text and "custom_key: keep-me" in text


def test_frontmatter_roundtrip_is_stable_and_orders_keys(project, load_store):
    p = write_spec(project, "SPEC-001", epic="EPIC-001", deps=["SPEC-000"])
    ws = load_store
    meta, body = ws.parse_frontmatter(p)
    once = ws.render_frontmatter(meta, body)
    p.write_text(once)
    meta2, body2 = ws.parse_frontmatter(p)
    assert ws.render_frontmatter(meta2, body2) == once
    keys = [line.split(":")[0] for line in once.split("---")[1].strip().splitlines()]
    assert keys == ["artifact_type", "work_item_id", "title", "status", "epic",
                    "source_of_truth", "dependencies"]


def test_QUIRK_frontmatter_comments_and_multiline_values_are_lost_on_rewrite(project, load_store):
    """QUIRK: the hand-rolled parser drops '#' comment lines and cannot hold multi-line values."""
    p = write_spec(project, "SPEC-001", extra="# a comment")
    load_store.WorkStore(project).set_status("SPEC-001", "DONE")
    assert "# a comment" not in p.read_text()


def test_summary_lines_grouping_and_filters(project, load_store):
    write_spec(project, "EPIC-001", kind="epic", status="IN_PROGRESS")
    write_spec(project, "SPEC-001", epic="EPIC-001", status="READY")
    write_spec(project, "SPEC-002", epic="EPIC-001", status="BLOCKED", extra="blocker: why")
    s = load_store.WorkStore(project)
    out = s.summary_lines("EPIC-001")
    assert [line for line in out if not line.startswith(" ")] == ["READY", "IN_PROGRESS", "BLOCKED"]  # fixed display order
    assert any("blocker=why" in line for line in out)
    assert s.summary_lines(only_status="DONE") == ["No matching durable work found."]
    assert s.summary_lines("NOPE") == ["No matching durable work found."]


def test_epic_status_is_never_derived_from_children(project, load_store):
    """Documents that nothing updates an epic when its specs finish (PR 6 'Epic completion' is new)."""
    write_spec(project, "EPIC-001", kind="epic", status="DRAFT")
    write_spec(project, "SPEC-001", epic="EPIC-001", status="READY")
    s = load_store.WorkStore(project)
    s.set_status("SPEC-001", "DONE")
    assert s.get("EPIC-001").status == "DRAFT"
