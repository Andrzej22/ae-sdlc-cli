"""Remote-backend setup, reached from ./ae-sdlc through the hidden `ae-sdlc _work` command."""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .work.backend import WorkBackendError, WorkConfigError
from .work.config import read_config, set_backend, write_config
from .work.jira_status import BLOCKED_LABEL, propose_mapping
from .work.mcp import auth
from .work.mcp.capabilities import invoke, operation
from .work.mcp.client import McpClient, provider_endpoint
from .work.mcp.doctor import healthy, rows, run_doctor
from .work.mcp.errors import CredentialsMissing, InvalidResponse

_JIRA_REQUIRED = ("status_draft", "status_ready", "status_in_progress", "status_done")


def _warn_on_version_skew(root: Path) -> None:
    try:
        project = json.loads((root / ".ae-sdlc" / "manifest.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return
    if project and project != __version__:
        print(f"warning: project scripts are version {project}, installed package is {__version__}", file=sys.stderr)


def _choose(label: str, options: list[tuple[str, str]]) -> str:
    """Return the value picked from (value, description) pairs. A single option needs no prompt."""
    if not options:
        raise InvalidResponse(f"no {label} is visible to this credential")
    if len(options) == 1:
        print(f"{label}: {options[0][1]}")
        return options[0][0]
    if not sys.stdin.isatty():
        raise CredentialsMissing(f"more than one {label} is available; run this command in a terminal to choose")
    print(f"Available {label} options:")
    for i, (_, description) in enumerate(options, 1):
        print(f"  {i}. {description}")
    while True:
        raw = input(f"Choose {label} [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("Enter one of the listed numbers.")


def _prompt_values(provider: str) -> dict[str, str]:
    if provider == "linear":
        return {"api_key": getpass.getpass("Linear API key: ").strip()}
    if input("Atlassian credential: 1. personal API token  2. service-account key [1]: ").strip() == "2":
        return {"service_account_key": getpass.getpass("Service-account key: ").strip()}
    return {"email": input("Atlassian account email: ").strip(), "api_token": getpass.getpass("API token: ").strip()}


def _obtain_credential(provider: str) -> tuple[auth.Credential, dict[str, str] | None]:
    """Return the credential and, when newly typed, the values to store once they are verified."""
    existing = auth.resolve_optional(provider)
    if existing is not None:
        if existing.source == "env" or not sys.stdin.isatty():
            return existing, None
        if input("Replace the stored credentials? [y/N]: ").strip().lower() != "y":
            return existing, None
    elif not sys.stdin.isatty():
        return auth.resolve(provider), None  # raises the actionable "not found" error
    values = _prompt_values(provider)
    credential = auth.build_credential(provider, values, "prompt")
    if credential is None:
        raise CredentialsMissing("the credential was left empty")
    return credential, values


def _discover_linear(client: McpClient) -> dict[str, str]:
    teams = rows(client.call("list_teams", {"limit": 250}, retry_safe=True).data, "teams", "nodes")
    return {"team": _choose("team", [(str(t["name"]), str(t["name"])) for t in teams if t.get("name")])}


def _confirm_statuses(statuses: list[dict]) -> dict[str, str]:
    """Save a mapping a person approved; without a terminal, only a complete proposal is accepted."""
    proposal = propose_mapping(statuses)
    complete = all(key in proposal for key in _JIRA_REQUIRED)
    print("Proposed status mapping:")
    for key in (*_JIRA_REQUIRED, "status_blocked"):
        print(f"  {key} = {proposal.get(key, '(none)')}")
    if not sys.stdin.isatty():
        if not complete:
            raise WorkConfigError("the status mapping is incomplete; run this command in a terminal to choose")
        return proposal
    if complete and input("Use this mapping? [Y/n]: ").strip().lower() != "n":
        return proposal
    names = [(str(s["name"]), str(s["name"])) for s in statuses if s.get("name")]
    chosen = {key: _choose(key, names) for key in _JIRA_REQUIRED}
    blocked = _choose("status_blocked", [*names, ("", f"no status: use the {BLOCKED_LABEL} label")])
    return {**chosen, **({"status_blocked": blocked} if blocked else {})}


def _discover_jira(client: McpClient) -> dict[str, str]:
    sites = rows(client.call("getAccessibleAtlassianResources", {}, retry_safe=True).data, "resources", "sites")
    cloud_id = _choose("Atlassian site", [(str(s["cloudId"]), str(s.get("url") or s["cloudId"]))
                                          for s in sites if s.get("cloudId")])
    projects = invoke(client, operation("jira", "list_projects"), {"maxResults": 100},
                      cloud_id=cloud_id, retry_safe=True).data
    key = _choose("Jira project", [(str(p["key"]), f"{p['key']} — {p.get('name', '')}")
                                   for p in rows(projects, "values", "projects") if p.get("key")])
    statuses = invoke(client, operation("jira", "list_statuses"), {"mode": "project", "projectKey": key},
                      cloud_id=cloud_id, retry_safe=True).data
    return {"cloud_id": cloud_id, "project_key": key, **_confirm_statuses(rows(statuses, "statuses"))}


def connect(root: Path, provider: str) -> int:
    credential, values = _obtain_credential(provider)
    with McpClient(provider_endpoint(provider), credential) as client:
        settings = _discover_linear(client) if provider == "linear" else _discover_jira(client)
    if values is not None:
        path = auth.save(provider, values)
        print(f"Credentials stored in {path} (mode 600)")
    config = read_config(root)
    config[provider] = settings
    write_config(root, config)
    print(f"Connected {provider}. Next: ./ae-sdlc backend doctor, then ./ae-sdlc backend use {provider}")
    return 0


def _settings(root: Path, provider: str) -> dict[str, str]:
    section = read_config(root).get(provider, {})
    return section if isinstance(section, dict) else {}


def _print_checks(checks: list[Any]) -> None:
    for check in checks:
        print(f"{check.state:8} {check.name}" + (f" — {check.detail}" if check.detail else ""))


def doctor(root: Path, provider: str) -> int:
    checks = run_doctor(provider, _settings(root, provider))
    _print_checks(checks)
    print("Write operations are verified against the tool catalog only; the doctor never creates data.")
    return 0 if healthy(checks) else 1


def use(root: Path, provider: str) -> int:
    checks = run_doctor(provider, _settings(root, provider))
    if not healthy(checks):
        _print_checks(checks)
        print(f"Backend not changed: {provider} failed the checks above.", file=sys.stderr)
        return 1
    set_backend(root, provider)
    print(f"Active backend: {provider}")
    return 0


def run(root: Path, action: str, provider: str) -> int:
    _warn_on_version_skew(root)
    try:
        return {"connect": connect, "doctor": doctor, "use": use}[action](root, provider)
    except WorkBackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130
