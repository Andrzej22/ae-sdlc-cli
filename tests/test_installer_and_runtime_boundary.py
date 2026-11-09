"""Installer/update-mechanism invariants and the generated-runtime dependency boundary (PR 0)."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from ae_sdlc import installer
from conftest import run_entry, write_spec

ALL = list(installer.PROVIDERS)


def test_init_is_idempotent(tmp_path):
    installer.apply(installer.build_plan(ALL, tmp_path), tmp_path)
    r = installer.apply(installer.build_plan(ALL, tmp_path), tmp_path)
    assert r.created == [] and r.conflicts == [] and r.unchanged


def test_existing_file_is_never_modified_and_gets_sidecar(tmp_path):
    (tmp_path / "AGENTS.md").write_text("mine\n")
    r = installer.apply(installer.build_plan(["claude"], tmp_path), tmp_path)
    assert (tmp_path / "AGENTS.md").read_text() == "mine\n"
    assert "AGENTS.md.ae_sdlc" in r.conflicts and (tmp_path / "AGENTS.md.ae_sdlc").exists()


def test_user_edited_runtime_script_is_preserved_on_rerun(tmp_path):
    installer.apply(installer.build_plan(["claude"], tmp_path), tmp_path)
    f = tmp_path / "scripts/ae-sdlc/work_store.py"
    f.write_text(f.read_text() + "\n# local edit\n")
    r = installer.apply(installer.build_plan(["claude"], tmp_path), tmp_path)
    assert "# local edit" in f.read_text()
    assert "scripts/ae-sdlc/work_store.py.ae_sdlc" in r.conflicts


def test_manifest_merges_providers_and_is_updated_in_place(tmp_path):
    installer.apply(installer.build_plan(["claude"], tmp_path), tmp_path)
    r = installer.apply(installer.build_plan(["codex"], tmp_path), tmp_path)
    m = json.loads((tmp_path / ".ae-sdlc/manifest.json").read_text())
    assert m["providers"] == ["claude", "codex"]
    assert ".ae-sdlc/manifest.json.ae_sdlc" not in r.conflicts


def test_dry_run_writes_nothing(tmp_path):
    installer.apply(installer.build_plan(ALL, tmp_path), tmp_path, dry_run=True)
    assert list(tmp_path.iterdir()) == []


def test_entrypoint_is_executable(project):
    assert (project / "ae-sdlc").stat().st_mode & 0o111


def test_all_five_providers_install_and_validate(tmp_path):
    installer.apply(installer.build_plan(ALL, tmp_path), tmp_path)
    p = run_entry(tmp_path, "validate")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "0 error(s)" in p.stdout


@pytest.mark.parametrize("name", ALL)
def test_each_provider_alone_validates(tmp_path, name):
    installer.apply(installer.build_plan([name], tmp_path), tmp_path)
    p = run_entry(tmp_path, "validate")
    assert p.returncode == 0, p.stdout + p.stderr


def test_thin_runtime_forbidden_files_fail_validation(project):
    (project / ".ae-sdlc" / "quality-gates.json").write_text("{}")
    p = run_entry(project, "validate")
    assert p.returncode == 1 and "forbidden" in p.stdout


def test_status_command_output_for_local_backend(project):
    write_spec(project, "SPEC-001", title="Thing")
    p = run_entry(project, "status")
    assert p.returncode == 0
    assert p.stdout.splitlines() == ["READY", "  SPEC-001 [spec] Thing"]
    assert run_entry(project, "status", "--blocked").stdout.strip() == "No matching durable work found."


def test_execute_work_skills_forbid_helper_scripts_for_every_provider(tmp_path):
    """Thin-execution invariant PR 5 will narrowly amend; validate_harness warns when it disappears."""
    installer.apply(installer.build_plan(ALL, tmp_path), tmp_path)
    paths = [".claude/skills/execute-work/SKILL.md", ".agents/skills/execute-work/SKILL.md",
             ".cursor/skills/execute-work/SKILL.md", ".opencode/command/execute-work.md"]
    for rel in paths:
        t = (tmp_path / rel).read_text().lower()
        assert "do not call" in t and "scripts/ae-sdlc/*" in t, rel
    assert "do not call ./ae-sdlc" in installer_tail()


def installer_tail() -> str:
    sys.path.insert(0, str(installer.DATA / "core" / "scripts" / "ae-sdlc"))
    try:
        sys.modules.pop("providers", None)
        import providers
        return providers.BACKGROUND_TAIL + providers.EXECUTE_TAIL
    finally:
        sys.path.pop(0)


def test_generated_runtime_is_stdlib_only():
    """KEY BOUNDARY: ./ae-sdlc and scripts/ae-sdlc/** run under the PROJECT interpreter, which does not
    see pipx/uv-tool site-packages. They must stay stdlib-only; MCP deps belong in the ae_sdlc package."""
    core = installer.DATA / "core"
    files = [core / "ae-sdlc", *core.joinpath("scripts").rglob("*.py"),
             *installer.DATA.joinpath("providers").rglob("providers/*.py")]
    local = {"work_store", "providers", "run_workflow", "validate_harness", "claude", "codex",
             "cursor", "opencode", "agy"}
    bad = []
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for m in mods:
                top = m.split(".")[0]
                if top and top not in sys.stdlib_module_names and top not in local:
                    bad.append((f.name, m))
    assert bad == []


def test_generated_runtime_has_no_link_back_to_installed_package(project):
    """Today there is no mechanism for ./ae-sdlc to find the installed ae_sdlc package (PR 0 finding)."""
    text = "".join(p.read_text() for p in project.joinpath("scripts").rglob("*.py")) + \
           (project / "ae-sdlc").read_text()
    assert "ae_sdlc" not in text
