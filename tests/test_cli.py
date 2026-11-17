"""The installed `ae-sdlc` command is what a person runs; it drives the project file."""
from __future__ import annotations

import pytest

from ae_sdlc.cli import main
from conftest import write_spec


def test_help_lists_project_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "prepare-work" in out and "backend" in out


def test_init_points_at_the_installed_command(tmp_path, capsys):
    assert main(["init", "--providers", "claude", "--dir", str(tmp_path)]) == 0
    assert "Next: ae-sdlc validate" in capsys.readouterr().out
    assert (tmp_path / "ae-sdlc").is_file()


def test_installed_cli_runs_status_in_the_project(project, monkeypatch, capfd):
    write_spec(project, "SPEC-001", title="Thing")
    monkeypatch.chdir(project)
    assert main(["--provider", "claude", "status"]) == 0
    assert "SPEC-001" in capfd.readouterr().out


def test_installed_cli_finds_project_from_a_subdirectory(project, monkeypatch, capfd):
    write_spec(project, "SPEC-001", title="Thing")
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert main(["status", "--ready"]) == 0
    assert "SPEC-001" in capfd.readouterr().out


def test_installed_cli_dir_selects_the_project(project, tmp_path, monkeypatch, capfd):
    write_spec(project, "SPEC-001", title="Thing")
    monkeypatch.chdir(tmp_path)
    assert main(["--dir", str(project), "status"]) == 0
    assert "SPEC-001" in capfd.readouterr().out


def test_installed_cli_outside_a_project_explains_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="ae-sdlc init"):
        main(["status"])
