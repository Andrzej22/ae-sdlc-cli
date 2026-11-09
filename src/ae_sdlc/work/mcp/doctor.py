"""Validate a remote backend with real calls. A successful tools/list does not prove access.

The doctor only reads. Write operations are checked against the catalog and never exercised, because
a diagnostic must not create issues in a user's project.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..backend import WorkBackendError
from . import auth
from .capabilities import discover_catalog, invoke, is_available, operation, operations
from .client import McpClient, provider_endpoint
from .errors import AuthRejected, CredentialsMissing, McpFailure, PermissionDenied

OK, MISSING, DENIED, ERROR, SKIPPED, WARNING = "OK", "MISSING", "DENIED", "ERROR", "SKIPPED", "WARNING"
_FAILING = {MISSING, DENIED, ERROR}
_SETTINGS = {"linear": ("team",), "jira": ("cloud_id", "project_key")}


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str = ""


def healthy(checks: list[Check]) -> bool:
    return not any(c.state in _FAILING for c in checks)


def rows(data: Any, *keys: str) -> list[dict]:
    """Find the record list in a response: bare, under one of keys, or inside Atlassian's "data" envelope."""
    if isinstance(data, dict):
        if isinstance(data.get("data"), (dict, list)):
            data = data["data"]
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _failure_check(name: str, exc: WorkBackendError) -> Check:
    state = DENIED if isinstance(exc, (AuthRejected, PermissionDenied, CredentialsMissing)) else ERROR
    return Check(name, state, str(exc))


class _Probe:
    """Records one check per live call; the result is None when the call failed."""

    def __init__(self, checks: list[Check]):
        self.checks = checks

    def run(self, name: str, call: Callable[[], Any], detail: Callable[[Any], str] = lambda _: "") -> Any:
        try:
            result = call()
        except McpFailure as exc:
            self.checks.append(_failure_check(name, exc))
            return None
        self.checks.append(Check(name, OK, detail(result)))
        return result

    def skip(self, *names: str, reason: str) -> None:
        self.checks += [Check(name, SKIPPED, reason) for name in names]


def _names(data: Any, *keys: str) -> str:
    found = [str(row.get("name")) for row in rows(data, *keys) if row.get("name")]
    return ", ".join(found)


def _probe_linear(client: McpClient, settings: Mapping[str, str], probe: _Probe) -> None:
    team = settings["team"]
    def read(tool: str, **arguments: Any) -> Any:
        return client.call(tool, arguments, retry_safe=True).data
    probe.run("live: credential identity", lambda: read("get_user", query="me"))
    probe.run("live: status mapping", lambda: read("list_issue_statuses", team=team),
              lambda data: f"team statuses: {_names(data, 'statuses', 'nodes')}")
    listed = probe.run("live: project access", lambda: read("list_issues", team=team, limit=1))
    if listed is None:
        probe.skip("live: comments", "live: relationships", reason="project access failed")
        return
    issues = rows(listed, "issues", "nodes")
    if not issues:
        probe.skip("live: comments", "live: relationships", reason="the team has no issues to read")
        return
    issue_id = issues[0].get("identifier") or issues[0].get("id")
    probe.run("live: comments", lambda: read("list_comments", issueId=issue_id, limit=1))
    probe.run("live: relationships", lambda: read("get_issue", id=issue_id, includeRelations=True))


def _probe_jira(client: McpClient, settings: Mapping[str, str], probe: _Probe) -> None:
    cloud_id, key = settings["cloud_id"], settings["project_key"]
    def read(tool: str, **arguments: Any) -> Any:
        return client.call(tool, {"cloudId": cloud_id, **arguments}, retry_safe=True).data
    probe.run("live: credential identity", lambda: client.call("atlassianUserInfo", {}, retry_safe=True).data)
    listed = probe.run("live: project access",
                       lambda: read("searchJiraIssuesUsingJql", jql=f'project = "{key}" ORDER BY created DESC',
                                    maxResults=1))
    names = ("live: comments", "live: relationships", "live: status mapping")
    if listed is None:
        probe.skip(*names, reason="project access failed")
        return
    issues = rows(listed, "issues", "nodes")
    if not issues:
        probe.skip(*names, reason="the project has no issues to read")
        return
    issue = str(issues[0].get("key"))
    probe.run(names[0], lambda: read("listJiraIssueComments", issueIdOrKey=issue, maxResults=1))
    probe.run(names[1], lambda: read("getJiraIssue", issueIdOrKey=issue, view="evidence"))
    transitions = operation("jira", "list_transitions")
    probe.run(names[2],
              lambda: invoke(client, transitions, {"issueIdOrKey": issue}, cloud_id=cloud_id, retry_safe=True).data,
              lambda data: f"transitions: {_names(data, 'transitions')}")


def run_doctor(provider: str, settings: Mapping[str, str], env: Mapping[str, str] = os.environ) -> list[Check]:
    checks: list[Check] = []
    missing = [key for key in _SETTINGS[provider] if not settings.get(key)]
    if missing:
        checks.append(Check("configuration", MISSING, f"{', '.join(missing)} not set; "
                                                      f"run ./ae-sdlc backend connect {provider}"))
        return checks
    checks.append(Check("configuration", OK, ", ".join(f"{k}={settings[k]}" for k in _SETTINGS[provider])))
    try:
        credential = auth.resolve(provider, env)
        url = provider_endpoint(provider, env)
    except WorkBackendError as exc:
        checks.append(_failure_check("credentials", exc))
        return checks
    checks.append(Check("credentials", OK, f"source: {credential.source}"))
    try:
        with McpClient(url, credential) as client:
            checks.append(Check("connection", OK, url))
            catalog = discover_catalog(provider, url, credential, client)
            for op in operations(provider):
                if is_available(op, catalog):
                    checks.append(Check(f"tool: {op.name}", OK, op.tool))
                elif op.required:
                    checks.append(Check(f"tool: {op.name}", MISSING, f"{op.tool} is not visible to this credential"))
                else:
                    checks.append(Check(f"tool: {op.name}", WARNING,
                                        f"{op.tool} is not visible; this optional capability is unavailable"))
            probe = _Probe(checks)
            (_probe_linear if provider == "linear" else _probe_jira)(client, settings, probe)
    except McpFailure as exc:
        checks.append(_failure_check("connection", exc))
    return checks
