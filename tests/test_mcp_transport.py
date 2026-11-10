"""PR 2: MCP client, credentials, error classification, capabilities, doctor, and backend commands."""
from __future__ import annotations

import base64
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from ae_sdlc import backend_cli
from ae_sdlc.work.backend import WorkConfigError
from ae_sdlc.work.config import backend_name, read_config, render_toml, set_backend, write_config
from ae_sdlc.work.mcp import auth
from ae_sdlc.work.mcp.capabilities import invoke, is_available, operation, operations
from ae_sdlc.work.mcp.client import McpClient, provider_endpoint
from ae_sdlc.work.mcp.doctor import DENIED, MISSING, OK, SKIPPED, healthy, run_doctor
from ae_sdlc.work.mcp.errors import (AuthRejected, CredentialsMissing, InvalidResponse, PermissionDenied, RateLimited,
                                     ToolFailed, TransportFailure, failure_from_tool, parse_payload)
from conftest import run_entry
from fake_mcp_server import serve

SECRET = "good-token"
GOOD = auth.Credential(f"Bearer {SECRET}", "env", [SECRET])
BAD = auth.Credential("Bearer wrong", "env", ["wrong"])


def echo(value: str) -> str:
    return json.dumps({"value": value})


def tool_error() -> str:
    raise ValueError("Could not find state")


def jira_style_failure() -> str:
    return json.dumps({"error": "TRANSITION_FAILED", "status": 400, "allowedValues": ["Done"]})


def slow() -> str:
    time.sleep(3)
    return "late"


def fast_client(url, credential=GOOD, **kwargs):
    return McpClient(url, credential, **{"timeout": 5.0, "retries": 2, "sleep": lambda _: None, **kwargs})


# --- real transport ---

def test_session_lists_tools_and_reuses_one_connection_for_many_calls():
    with serve({"echo": echo}) as (url, controls):
        with fast_client(url) as client:
            assert [t.name for t in client.list_tools()] == ["echo"]
            assert client.call("echo", {"value": "a"}).data == {"value": "a"}
            assert client.call("echo", {"value": "b"}).data == {"value": "b"}
    assert controls.calls == ["echo", "echo"]
    assert set(controls.seen_auth) == {"Bearer good-token"}


def test_invalid_credential_is_auth_failure_and_is_not_retried():
    slept = []
    with serve({"echo": echo}) as (url, controls):
        with pytest.raises(AuthRejected) as caught:
            with McpClient(url, BAD, timeout=5.0, sleep=slept.append):
                pass
    assert slept == [] and len(controls.seen_auth) == 1
    assert "wrong" not in str(caught.value)


def test_unreachable_server_is_transport_failure_after_bounded_retries():
    slept = []
    with pytest.raises(TransportFailure):
        with McpClient("http://127.0.0.1:9/mcp", GOOD, timeout=1.0, retries=2, sleep=slept.append):
            pass
    assert slept == [0.5, 1.0]


def test_tool_error_is_classified_and_session_stays_usable():
    with serve({"bad": tool_error, "echo": echo}) as (url, _):
        with fast_client(url) as client:
            with pytest.raises(ToolFailed, match="Error executing tool bad"):
                client.call("bad", {})
            assert client.call("echo", {"value": "x"}).data == {"value": "x"}


def test_payload_error_without_is_error_is_not_treated_as_success():
    with serve({"transition": jira_style_failure}) as (url, _):
        with fast_client(url) as client:
            with pytest.raises(ToolFailed, match="TRANSITION_FAILED"):
                client.call("transition", {})


def test_rate_limit_retries_write_and_honors_retry_after():
    slept = []
    with serve({"echo": echo}) as (url, controls):
        controls.http_failures.append((429, {"Retry-After": "7"}))
        with fast_client(url, sleep=slept.append) as client:
            assert client.call("echo", {"value": "ok"}).data == {"value": "ok"}
    assert slept == [7.0]


def test_rate_limit_gives_up_after_retries():
    with serve({"echo": echo}) as (url, controls):
        controls.http_failures += [(429, {})] * 5
        with fast_client(url) as client:
            with pytest.raises(RateLimited):
                client.call("echo", {"value": "x"})


def test_server_error_retries_a_read_but_never_a_write():
    with serve({"echo": echo}) as (url, controls):
        with fast_client(url) as client:
            controls.http_failures.append((503, {}))
            assert client.call("echo", {"value": "r"}, retry_safe=True).data == {"value": "r"}
            controls.calls.clear()
            controls.http_failures.append((503, {}))
            with pytest.raises(TransportFailure):
                client.call("echo", {"value": "w"})
            assert controls.calls == ["echo"]
            assert client.call("echo", {"value": "after"}, retry_safe=True).data == {"value": "after"}


def test_request_timeout_is_transport_failure():
    with serve({"slow": slow}) as (url, _):
        with McpClient(url, GOOD, timeout=0.5, retries=0) as client:
            with pytest.raises(TransportFailure):
                client.call("slow", {})


# --- response classification ---

def test_payload_parser_ignores_plain_text_trailer():
    assert parse_payload('{"a": 1}\nUse executeRead to run these.') == {"a": 1}
    assert parse_payload("Could not find state") is None
    assert parse_payload("5 issues found") is None


@pytest.mark.parametrize("text,kind,fragment", [
    ('{"statusCode": 401, "message": "Unauthorized"}', AuthRejected, "rejected"),
    ('{"statusCode": 401, "message": "token is missing the scope claim"}', PermissionDenied, "scoped token"),
    ("You don't have permission to connect via API token", PermissionDenied, "organization admin"),
    ('{"status": 403, "message": "no"}', PermissionDenied, "lacks permission"),
    ('{"statusCode": 429}', RateLimited, "rate limited"),
])
def test_tool_failures_get_distinct_actionable_classes(text, kind, fragment):
    failure = failure_from_tool("t", text, True)
    assert isinstance(failure, kind) and fragment in str(failure)


def test_issue_payload_with_non_numeric_status_is_success():
    assert failure_from_tool("t", '{"key": "A-1", "status": "Done"}', False) is None


def test_non_json_response_fails_require():
    from ae_sdlc.work.mcp.client import ToolResponse
    with pytest.raises(InvalidResponse):
        ToolResponse("plain", None).require(dict, "t")


# --- endpoint ---

def test_endpoint_defaults_and_env_override():
    assert provider_endpoint("linear", {}) == "https://mcp.linear.app/mcp"
    assert provider_endpoint("jira", {}) == "https://mcp.atlassian.com/v2/mcp"
    assert provider_endpoint("jira", {"AE_SDLC_JIRA_MCP_URL": "http://127.0.0.1:1/m"}).endswith("/m")


def test_endpoint_refuses_plain_http_to_remote_host():
    with pytest.raises(WorkConfigError):
        provider_endpoint("linear", {"AE_SDLC_LINEAR_MCP_URL": "http://evil.example/mcp"})


# --- credentials ---

def test_env_wins_over_file(tmp_path):
    env = {"AE_SDLC_CREDENTIALS_FILE": str(tmp_path / "c.toml"), "LINEAR_API_KEY": "from-env"}
    auth.save("linear", {"api_key": "from-file"}, env)
    c = auth.resolve("linear", env)
    assert c.source == "env" and c.authorization == "Bearer from-env"


def test_atlassian_header_forms():
    basic = auth.resolve("jira", {"ATLASSIAN_EMAIL": "a@b", "ATLASSIAN_API_TOKEN": "t", "AE_SDLC_CREDENTIALS_FILE": "/nonexistent"})
    assert basic.authorization == "Basic " + base64.b64encode(b"a@b:t").decode()
    assert auth.resolve("jira", {"ATLASSIAN_SERVICE_ACCOUNT_KEY": "k"}).authorization == "Bearer k"


def test_saved_file_is_private_and_keeps_other_provider(tmp_path):
    env = {"AE_SDLC_CREDENTIALS_FILE": str(tmp_path / "dir" / "c.toml")}
    auth.save("linear", {"api_key": 'qu"ote'}, env)
    path = auth.save("jira", {"service_account_key": "k"}, env)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth.resolve("linear", env).authorization == 'Bearer qu"ote'
    assert auth.resolve("jira", env).source == "file"


def test_group_readable_file_is_refused_with_fix(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[linear]\napi_key = "x"\n')
    path.chmod(0o640)
    with pytest.raises(CredentialsMissing, match="chmod 600"):
        auth.resolve("linear", {"AE_SDLC_CREDENTIALS_FILE": str(path)})


def test_malformed_file_error_does_not_quote_contents(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("api_key = sekret-value\n")
    path.chmod(0o600)
    with pytest.raises(CredentialsMissing) as caught:
        auth.resolve("linear", {"AE_SDLC_CREDENTIALS_FILE": str(path)})
    assert "sekret" not in str(caught.value)


def test_missing_credentials_say_what_to_do(tmp_path):
    with pytest.raises(CredentialsMissing, match="LINEAR_API_KEY"):
        auth.resolve("linear", {"AE_SDLC_CREDENTIALS_FILE": str(tmp_path / "none.toml")})


def test_credential_repr_hides_secret():
    assert SECRET not in repr(GOOD)


# --- config ---

def test_set_backend_preserves_provider_settings(tmp_path):
    (tmp_path / ".ae-sdlc").mkdir()
    write_config(tmp_path, {"linear": {"team": 'A "quoted" team'}})
    set_backend(tmp_path, "linear")
    assert backend_name(tmp_path) == "linear"
    assert read_config(tmp_path)["linear"]["team"] == 'A "quoted" team'


PLAN_EXAMPLE = """[work]
backend = "local"

[work.linear]
team = "ENG"

[work.context]
max_related_items = 5
max_comments_per_item = 20

[work.execution]
update_status = true
read_comments = true
allow_related_comments = true
"""


def test_backend_switch_keeps_nested_tables_and_non_string_values(project):
    """The example configuration in docs/Implementation_Plan.md used to make every rewrite fail."""
    import tomllib
    cfg = project / ".ae-sdlc" / "work.toml"
    cfg.write_text(PLAN_EXAMPLE + '\n[linear]\nteam = "Core"\nlimits = [1, 2.5]\nsince = 2026-11-10\n'
                   '"odd key" = { nested = "x" }\n', encoding="utf-8")
    before = tomllib.loads(cfg.read_text(encoding="utf-8"))
    p = run_entry(project, "backend", "use", "local")
    assert p.returncode == 0, p.stderr
    after = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert after == before


def test_render_toml_round_trips_unicode():
    import tomllib
    data = {"t": {"k": "Команда\n\\ 🚀"}}
    assert tomllib.loads(render_toml(data)) == data


# --- capabilities ---

def test_deferred_operation_needs_its_gateway_tool():
    op = operation("jira", "list_transitions")
    assert not is_available(op, {"listJiraIssueTransitions"})
    assert is_available(op, {"listJiraIssueTransitions", "executeRead"})


def test_deferred_invocation_goes_through_gateway():
    class Recorder:
        def call(self, tool, arguments, retry_safe=False):
            self.seen = (tool, arguments)
    r = Recorder()
    invoke(r, operation("jira", "list_transitions"), {"issueIdOrKey": "A-1"}, cloud_id="c")
    assert r.seen == ("executeRead", {"cloudId": "c", "name": "listJiraIssueTransitions", "inputs": {"issueIdOrKey": "A-1"}})


def test_every_provider_declares_core_required_operations():
    for provider in ("linear", "jira"):
        required = {op.name for op in operations(provider) if op.required}
        assert {"read_issue", "search_issues", "create_issue", "update_issue", "transition_issue",
                "read_comments", "add_comment", "read_dependencies"} <= required


def test_rows_unwraps_bare_keyed_and_enveloped_responses():
    from ae_sdlc.work.mcp.doctor import rows
    row = {"id": "1"}
    assert rows([row], "x") == rows({"x": [row]}, "x") == rows({"data": {"x": [row]}}, "x") == rows({"data": [row]}, "x") == [row]


# --- doctor against a server with Linear's tool names ---

def linear_tools(issues=True, projects=()):
    def get_issue(id: str, includeRelations: bool = False) -> str:
        return json.dumps({"id": id})

    def list_issues(team: str, limit: int = 1) -> str:
        return json.dumps({"issues": [{"id": "1", "identifier": "T-1"}] if issues else []})

    def save_issue() -> str:
        raise AssertionError("the doctor must not write")

    def save_comment() -> str:
        raise AssertionError("the doctor must not write")

    def list_issue_statuses(team: str) -> str:
        return json.dumps([{"name": "Backlog", "type": "backlog"}, {"name": "Todo", "type": "unstarted"},
                           {"name": "Doing", "type": "started"}, {"name": "Done", "type": "completed"}])

    def get_project(query: str) -> str:
        if query not in projects:
            raise ValueError(f"Entity not found: Project {query}")
        return json.dumps({"id": query, "name": query})

    def list_projects(team: str, limit: int = 50) -> str:
        return json.dumps({"projects": [{"id": f"P-{i}", "name": name} for i, name in enumerate(projects)]})

    def get_team(query: str) -> str:
        return json.dumps({"id": query})

    def list_issue_labels(team: str) -> str:
        return json.dumps({"labels": []})

    def save_issue_label() -> str:
        raise AssertionError("the doctor must not write")

    def list_comments(issueId: str, limit: int = 1) -> str:
        return json.dumps({"comments": []})

    def get_user(query: str) -> str:
        return json.dumps({"id": "me"})

    def list_teams(limit: int = 50) -> str:
        return json.dumps({"teams": [{"id": "t", "name": "Core"}]})

    return {f.__name__: f for f in (get_issue, list_issues, save_issue, save_comment, list_issue_statuses,
                                    list_comments, get_user, list_teams, get_project, list_projects, get_team,
                                    list_issue_labels, save_issue_label)}


def linear_env(url, tmp_path, key=SECRET):
    return {"LINEAR_API_KEY": key, "AE_SDLC_LINEAR_MCP_URL": url, "AE_SDLC_CREDENTIALS_FILE": str(tmp_path / "none")}


def by_name(checks):
    return {c.name: c for c in checks}


def test_doctor_healthy_never_writes_and_reports_source(tmp_path):
    with serve(linear_tools()) as (url, controls):
        checks = run_doctor("linear", {"team": "Core"}, linear_env(url, tmp_path))
    found = by_name(checks)
    assert healthy(checks), checks
    assert found["credentials"].detail == "source: env"
    assert "Todo" in found["live: status mapping"].detail
    assert found["tool: create_epic"].state == "WARNING"  # optional save_project absent
    assert "save_issue" not in controls.calls and "save_comment" not in controls.calls
    assert SECRET not in repr(checks)


def test_doctor_reports_missing_required_tool(tmp_path):
    tools = linear_tools()
    del tools["save_comment"]
    with serve(tools) as (url, _):
        checks = run_doctor("linear", {"team": "Core"}, linear_env(url, tmp_path))
    assert not healthy(checks)
    assert by_name(checks)["tool: add_comment"].state == MISSING


def test_doctor_rejected_credential_is_denied(tmp_path):
    with serve(linear_tools()) as (url, _):
        checks = run_doctor("linear", {"team": "Core"}, linear_env(url, tmp_path, key="wrong"))
    assert by_name(checks)["connection"].state == DENIED and not healthy(checks)


def test_doctor_tool_result_auth_failure_after_list_succeeds(tmp_path):
    """tools/list success does not prove access: the real call is what fails."""
    tools = linear_tools()

    def get_user(query: str) -> str:
        return json.dumps({"statusCode": 401, "message": "Unauthorized"})
    tools["get_user"] = get_user
    with serve(tools) as (url, _):
        checks = run_doctor("linear", {"team": "Core"}, linear_env(url, tmp_path))
    assert by_name(checks)["live: credential identity"].state == DENIED and not healthy(checks)


def test_doctor_skips_issue_checks_on_empty_team(tmp_path):
    with serve(linear_tools(issues=False)) as (url, _):
        checks = run_doctor("linear", {"team": "Core"}, linear_env(url, tmp_path))
    assert by_name(checks)["live: comments"].state == SKIPPED and healthy(checks)


def test_doctor_checks_a_pinned_project(tmp_path):
    with serve(linear_tools(projects=["Sandbox"])) as (url, _):
        pinned = run_doctor("linear", {"team": "Core", "project": "Sandbox"}, linear_env(url, tmp_path))
        missing = run_doctor("linear", {"team": "Core", "project": "Gone"}, linear_env(url, tmp_path))
    assert healthy(pinned) and by_name(pinned)["live: pinned project"].state == "OK"
    assert "project=Sandbox" in by_name(pinned)["configuration"].detail
    assert not healthy(missing) and by_name(missing)["live: pinned project"].state == "ERROR"


def test_doctor_without_settings_points_to_connect(tmp_path):
    checks = run_doctor("linear", {}, {})
    assert checks[0].state == MISSING and "backend connect linear" in checks[0].detail


# --- commands ---

def test_use_refuses_unhealthy_backend_and_keeps_current(tmp_path, monkeypatch, capsys):
    (tmp_path / ".ae-sdlc").mkdir()
    monkeypatch.setenv("AE_SDLC_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    write_config(tmp_path, {"linear": {"team": "Core"}})
    assert backend_cli.run(tmp_path, "use", "linear") == 1
    assert backend_name(tmp_path) == "local"
    assert "Backend not changed" in capsys.readouterr().err


def test_connect_discovers_team_and_stores_settings_not_secrets(tmp_path, monkeypatch, capsys):
    (tmp_path / ".ae-sdlc").mkdir()
    cred_file = tmp_path / "c.toml"
    with serve(linear_tools()) as (url, _):
        monkeypatch.setenv("AE_SDLC_LINEAR_MCP_URL", url)
        monkeypatch.setenv("AE_SDLC_CREDENTIALS_FILE", str(cred_file))
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: True)})())
        monkeypatch.setattr(backend_cli.getpass, "getpass", lambda _: SECRET)
        assert backend_cli.run(tmp_path, "connect", "linear") == 0
    assert read_config(tmp_path)["linear"] == {"team": "Core"}
    assert SECRET not in (tmp_path / ".ae-sdlc" / "work.toml").read_text()
    assert stat.S_IMODE(cred_file.stat().st_mode) == 0o600
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize("terminal, answer, expected", [
    (True, "3", {"team": "Core", "project": "Sandbox"}),  # listed alphabetically after "none" and "Other"
    (True, "1", {"team": "Core"}),
    (False, None, {"team": "Core"}),
], ids=["choose-project", "choose-none", "no-terminal"])
def test_connect_offers_the_team_projects(tmp_path, monkeypatch, capsys, terminal, answer, expected):
    (tmp_path / ".ae-sdlc").mkdir()
    with serve(linear_tools(projects=["Sandbox", "Other"])) as (url, _):
        monkeypatch.setenv("AE_SDLC_LINEAR_MCP_URL", url)
        monkeypatch.setenv("AE_SDLC_CREDENTIALS_FILE", str(tmp_path / "none"))
        monkeypatch.setenv("LINEAR_API_KEY", SECRET)
        monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: terminal)})())
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert backend_cli.run(tmp_path, "connect", "linear") == 0
    assert read_config(tmp_path)["linear"] == expected


def test_connect_with_rejected_credential_stores_nothing(tmp_path, monkeypatch, capsys):
    (tmp_path / ".ae-sdlc").mkdir()
    cred_file = tmp_path / "c.toml"
    with serve(linear_tools()) as (url, _):
        monkeypatch.setenv("AE_SDLC_LINEAR_MCP_URL", url)
        monkeypatch.setenv("AE_SDLC_CREDENTIALS_FILE", str(cred_file))
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": staticmethod(lambda: True)})())
        monkeypatch.setattr(backend_cli.getpass, "getpass", lambda _: "wrong")
        assert backend_cli.run(tmp_path, "connect", "linear") == 1
    assert not cred_file.exists()
    assert "rejected" in capsys.readouterr().err


# --- generated ./ae-sdlc, no package required for local ---

def bare_env(tmp_path):
    return {"PATH": str(tmp_path / "empty-bin")}


def test_local_backend_commands_work_without_package(project):
    env = bare_env(project)
    assert run_entry(project, "backend", "show", env=env).stdout.strip() == "Active backend: local"
    p = run_entry(project, "backend", "doctor", env=env)
    assert p.returncode == 0 and "no credentials" in p.stdout
    p = run_entry(project, "backend", "use", "local", env=env)
    assert p.returncode == 0 and (project / ".ae-sdlc/work.toml").exists()
    assert run_entry(project, "status", env=env).returncode == 0


def test_remote_command_without_package_explains_install(project):
    p = run_entry(project, "backend", "connect", "linear", env=bare_env(project))
    assert p.returncode != 0 and "pipx" in p.stderr


def test_generated_entrypoint_delegates_to_package(project, tmp_path):
    env = {"PATH": str(tmp_path / "none"), "HOME": str(tmp_path), "AE_SDLC_BIN": f"{sys.executable} -m ae_sdlc.cli",
           "AE_SDLC_CREDENTIALS_FILE": str(tmp_path / "none.toml")}
    p = run_entry(project, "backend", "use", "linear", env=env)
    assert p.returncode == 1 and "Backend not changed" in p.stderr
    assert run_entry(project, "backend", "show", env=env).stdout.strip() == "Active backend: local"


def test_provider_env_scrub_removes_only_tracker_credentials(project, monkeypatch, load_store):
    import providers
    monkeypatch.setenv("LINEAR_API_KEY", "x")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "y")
    monkeypatch.setenv("KEEP_ME", "z")
    providers.remove_tracker_credentials_from_env()
    import os
    assert "LINEAR_API_KEY" not in os.environ and "ATLASSIAN_API_TOKEN" not in os.environ
    assert os.environ["KEEP_ME"] == "z"


def test_a_connection_that_never_answers_fails_within_the_timeout():
    import asyncio
    from types import SimpleNamespace

    from ae_sdlc.work.mcp.client import McpClient
    from ae_sdlc.work.mcp.errors import TransportFailure

    class Stalled(McpClient):
        async def _serve(self, ready):
            await asyncio.Event().wait()

    client = Stalled("https://mcp.example.test/mcp", SimpleNamespace(authorization="x", secrets=[]), timeout=0.05,
                     retries=0)
    with pytest.raises(TransportFailure, match="no response within"):
        client._ensure_connected()


# --- interactive prepare-work / execute-work keep tracker access without exposing the secret ---

CREDENTIAL_PROBE_AGENT = """#!/bin/sh
file="$AE_SDLC_CREDENTIALS_FILE"
echo "$file" > "$PWD/seen-path"
leak=no; [ -n "$LINEAR_API_KEY" ] && leak=yes
found=no; [ -n "$file" ] && grep -q 'api_key = "secret-key"' "$file" && found=yes
mode=none; [ -n "$file" ] && mode=$(stat -c %a "$file")
printf '{"is_error": false, "structured_output": {"status": "COMPLETED", "summary": "leak=%s found=%s mode=%s", "blockers": []}}' "$leak" "$found" "$mode"
"""


def _run_agent_command(project, tmp_path, backend_toml, extra_env):
    stubs = tmp_path / "agent-bin"
    stubs.mkdir()
    agent = stubs / "claude"
    agent.write_text(CREDENTIAL_PROBE_AGENT, encoding="utf-8")
    agent.chmod(0o755)
    (project / ".ae-sdlc" / "work.toml").write_text(backend_toml, encoding="utf-8")
    env = {"PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path), **extra_env}
    p = run_entry(project, "--provider", "claude", "execute-work", "AND-1", env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)["summary"], (project / "seen-path").read_text().strip()


def test_execute_work_moves_env_credentials_to_a_private_file_for_work_commands(project, tmp_path):
    summary, path = _run_agent_command(project, tmp_path, '[work]\nbackend = "linear"\n\n[linear]\nteam = "T"\n',
                                       {"LINEAR_API_KEY": "secret-key"})
    assert summary == "leak=no found=yes mode=600"
    assert path and not Path(path).exists(), "the temporary file is removed when the agent finishes"


def test_execute_work_keeps_the_users_credentials_file_without_env_credentials(project, tmp_path):
    summary, path = _run_agent_command(project, tmp_path, '[work]\nbackend = "linear"\n\n[linear]\nteam = "T"\n',
                                       {"AE_SDLC_CREDENTIALS_FILE": "/home/me/creds.toml"})
    assert path == "/home/me/creds.toml" and summary.startswith("leak=no")


def test_local_execute_work_only_removes_tracker_credentials(project, tmp_path):
    summary, path = _run_agent_command(project, tmp_path, '[work]\nbackend = "local"\n', {"LINEAR_API_KEY": "secret-key"})
    assert (summary, path) == ("leak=no found=no mode=none", "")
