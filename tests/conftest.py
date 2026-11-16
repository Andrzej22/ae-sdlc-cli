"""Shared fixtures: install the real harness into a temp project via the real installer."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ae_sdlc import installer


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with the claude+codex harness installed (no network, no agent CLIs)."""
    plan = installer.build_plan(["claude", "codex"], tmp_path)
    installer.apply(plan, tmp_path)
    return tmp_path


def write_spec(project: Path, work_id: str, *, status="READY", epic="", deps=None, title="T",
               kind="spec", body="# Body\n\nText.\n", extra: str = "") -> Path:
    deps = deps or []
    d = project / "docs" / "product" / ("epics" if epic or kind == "epic" else "standalone")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{work_id}.md"
    fm = [f"artifact_type: {kind}", f"work_item_id: {work_id}", f"title: {title}", f"status: {status}"]
    if kind == "spec":
        fm += [f"epic: {epic}", "source_of_truth: repository", f"dependencies: {deps!r}".replace("'", '"')]
    else:
        fm += ["source_of_truth: repository"]
    if extra:
        fm.append(extra)
    p.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body, encoding="utf-8")
    return p


def run_entry(project: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    """Run the generated ./ae-sdlc exactly as a user would (project interpreter, no ae_sdlc import)."""
    return subprocess.run([sys.executable, "-I", str(project / "ae-sdlc"), *args],
                          cwd=project, capture_output=True, text=True, env=env)


@pytest.fixture
def load_store(project, monkeypatch):
    """Import the installed work_store/run_workflow modules fresh, bound to the temp project."""
    scripts = str(project / "scripts" / "ae-sdlc")
    monkeypatch.syspath_prepend(scripts)
    for m in list(sys.modules):
        if m in ("work_store", "run_workflow", "providers", "work") or m.startswith("work."):
            sys.modules.pop(m, None)
    import work_store
    return work_store


@pytest.fixture(autouse=True)
def declared_sandbox(monkeypatch):
    """Tests run fake agents, so they declare the sandbox the unattended runner requires."""
    monkeypatch.setenv("AE_SDLC_SANDBOX", "1")


def agent_result(status: str = "COMPLETED", **fields) -> dict:
    """A complete execute-work result, as the schema requires; `fields` override the empty defaults."""
    return {"status": status, "work_items": [], "changed_files": [], "command_run": None, "blockers": [],
            "summary": "", "comments": [], **fields}
