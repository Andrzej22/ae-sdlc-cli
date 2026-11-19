"""Remote-backend setup, reached from ./ae-sdlc through the hidden `ae-sdlc _work` command."""
from __future__ import annotations

import getpass
import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__
from .work import commands
from .work.backend import WorkBackendError, WorkConfigError
from .work.config import backend_name, load_backend, read_config, set_backend, write_config
from .work.jira import JiraWorkBackend
from .work.jira_status import BLOCKED_LABEL, propose_mapping
from .work.linear import LinearWorkBackend
from .work.mcp import auth
from .work.mcp.capabilities import invoke, operation
from .work.mcp.client import McpClient, provider_endpoint
from .work.mcp.doctor import healthy, rows, run_doctor
from .work.mcp.errors import CredentialsMissing, InvalidResponse
from .work.runner import run_workflow as run_workflow_specs
from .work.service import WorkService

_REMOTE_BACKENDS = {"linear": LinearWorkBackend, "jira": JiraWorkBackend}
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


def _obtain_credential(provider: str, root: Path) -> tuple[auth.Credential, dict[str, str] | None]:
    """Return the credential and, when newly typed, the values to store once they are verified."""
    existing = auth.resolve_optional(provider, root=root)
    if existing is not None:
        if existing.source == "env" or not sys.stdin.isatty():
            return existing, None
        if input("Replace the stored credentials? [y/N]: ").strip().lower() != "y":
            return existing, None
    elif not sys.stdin.isatty():
        return auth.resolve(provider, root=root), None  # raises the actionable "not found" error
    values = _prompt_values(provider)
    credential = auth.build_credential(provider, values, "prompt")
    if credential is None:
        raise CredentialsMissing("the credential was left empty")
    return credential, values


def _pick_review(found: list[str], owner: str, section: str) -> dict[str, str]:
    """Offer a status named like "In Review" as the review step. It changes what DONE means, so a person decides."""
    if not found:
        return {}
    if not sys.stdin.isatty():
        print(f"review: {owner} has {found[0]!r}; run this command in a terminal to use it as the review step "
              f"(or set status_review under [{section}])")
        return {}
    print("Finished work can wait in a review state for a person to mark it Done, instead of going straight to Done.")
    name = _choose("review step", [("", "none: finished work goes straight to Done"),
                                   *((n, f"{n}: finished work waits here until a person marks it Done") for n in found)])
    return {"status_review": name} if name else {}


def _offer_review(client: McpClient, team: str) -> dict[str, str]:
    statuses = rows(client.call("list_issue_statuses", {"team": team}, retry_safe=True).data, "statuses", "nodes")
    found = [str(s["name"]) for s in statuses if s.get("type") == "started" and "review" in str(s.get("name")).lower()]
    return _pick_review(found, "the team", "linear")


def _offer_jira_review(statuses: list[dict], mapping: dict[str, str]) -> dict[str, str]:
    """Offer a project status named like "In Review" that the confirmed mapping does not already use."""
    taken = {name.lower() for name in mapping.values()}
    found = [str(s["name"]) for s in statuses if "review" in str(s.get("name", "")).lower()
             and str(s["name"]).lower() not in taken]
    return _pick_review(found, "the project", "jira")


def _discover_linear(client: McpClient) -> dict[str, str]:
    teams = rows(client.call("list_teams", {"limit": 250}, retry_safe=True).data, "teams", "nodes")
    team = _choose("team", [(str(t["name"]), str(t["name"])) for t in teams if t.get("name")])
    return {"team": team, **_discover_project(client, team), **_offer_review(client, team)}


def _discover_project(client: McpClient, team: str) -> dict[str, str]:
    found = rows(client.call("list_projects", {"team": team, "limit": 50}, retry_safe=True).data, "projects", "nodes")
    names = sorted({str(p["name"]) for p in found if p.get("name")})
    if not names:
        return {}
    if not sys.stdin.isatty():
        print("project: none, so each epic becomes its own Linear project "
              "(run this command in a terminal to choose one, or set project under [linear])")
        return {}
    project = _choose("project", [("", "none: each epic becomes its own Linear project"),
                                  *((name, f"{name}: all work goes here, epics are issues") for name in names)])
    return {"project": project} if project else {}


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
    listed = rows(statuses, "statuses")
    mapping = _confirm_statuses(listed)
    return {"cloud_id": cloud_id, "project_key": key, **mapping, **_offer_jira_review(listed, mapping)}


def connect(root: Path, provider: str) -> int:
    credential, values = _obtain_credential(provider, root)
    with McpClient(provider_endpoint(provider), credential) as client:
        settings = _discover_linear(client) if provider == "linear" else _discover_jira(client)
    if values is not None:
        path = auth.save_project(root, provider, values)
        print(f"Credentials stored in {path} (mode 600, ignored by git)")
    config = read_config(root)
    config[provider] = settings
    write_config(root, config)
    print(f"Connected {provider}. Next: ae-sdlc backend doctor, then ae-sdlc backend use {provider}")
    return 0


def _settings(root: Path, provider: str) -> dict[str, str]:
    section = read_config(root).get(provider, {})
    return section if isinstance(section, dict) else {}


def _print_checks(checks: list[Any]) -> None:
    for check in checks:
        print(f"{check.state:8} {check.name}" + (f" — {check.detail}" if check.detail else ""))


def doctor(root: Path, provider: str) -> int:
    checks = run_doctor(provider, _settings(root, provider), root=root)
    _print_checks(checks)
    print("Write operations are verified against the tool catalog only; the doctor never creates data.")
    return 0 if healthy(checks) else 1


def use(root: Path, provider: str) -> int:
    checks = run_doctor(provider, _settings(root, provider), root=root)
    if not healthy(checks):
        _print_checks(checks)
        print(f"Backend not changed: {provider} failed the checks above.", file=sys.stderr)
        return 1
    set_backend(root, provider)
    print(f"Active backend: {provider}")
    return 0


@contextmanager
def open_service(root: Path) -> Iterator[WorkService]:
    """Yield a service over the active backend, holding one MCP connection for a remote one."""
    name = backend_name(root)
    if name not in _REMOTE_BACKENDS:
        yield WorkService(load_backend(root), root=root)  # local, or the error naming an unknown backend
        return
    with McpClient(provider_endpoint(name), auth.resolve(name, root=root)) as client:
        yield WorkService(_REMOTE_BACKENDS[name](client, _settings(root, name)), root=root)


def run_work(root: Path, argv: list[str]) -> int:
    _warn_on_version_skew(root)
    return commands.execute(commands.parse(argv), lambda: open_service(root))


def run_workflow(root: Path, argv: list[str]) -> int:
    """Run a workflow over a remote backend, using the provider adapters installed in the project."""
    _warn_on_version_skew(root)
    sys.path.insert(0, str(root / "scripts" / "ae-sdlc"))
    providers = importlib.import_module("providers")
    try:
        name = backend_name(root)
        credential = auth.resolve(name, root=root)  # once, because agents must not inherit it from the environment
    except WorkBackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    providers.remove_tracker_credentials_from_env()

    @contextmanager
    def services() -> Iterator[WorkService]:
        with McpClient(provider_endpoint(name), credential) as client:
            yield WorkService(_REMOTE_BACKENDS[name](client, _settings(root, name)), root=root)

    return run_workflow_specs(root, argv, providers, services)


def run_status(root: Path, scope: str | None, only_status: str | None) -> int:
    _warn_on_version_skew(root)
    try:
        with open_service(root) as service:
            lines = service.status_lines(scope, only_status=only_status)
    except WorkBackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    print("\n".join(lines))
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
