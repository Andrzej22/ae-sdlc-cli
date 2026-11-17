"""Provider adapters. Only the adapters selected at `ae-sdlc init` time are installed."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from work.config import atomic_write, render_toml

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".ae-sdlc" / "manifest.json"

PREPARE_TAIL = (
    "Non-interactive invocation. If blocking input is missing, persist the question "
    "and return NEEDS_INPUT; do not invent an answer."
)
EXECUTE_TAIL = (
    "Thin execution mode. Do not call harness helper scripts other than the "
    "ae-sdlc work commands the skill names, or start quality/review/verification phases."
)
BACKGROUND_TAIL = (
    "Background workflow constraints:\n"
    "- runner_owns_status=true\n"
    "- implement only this spec\n"
    "- do not call ae-sdlc or scripts/ae-sdlc/*\n"
    "- do not spawn subagents or run quality/review/verification phases\n"
    "- comments in the result: only a finding someone must act on, stating what and why, for this "
    "spec or an item linked to it; otherwise an empty list\n"
    "- return the structured result and stop"
)


def remove_tracker_credentials_from_env() -> None:
    """Agent subprocesses inherit this environment and must not receive tracker credentials."""
    for name in [n for n in os.environ if n.startswith(("LINEAR_", "ATLASSIAN_"))]:
        del os.environ[name]


# Per backend: the credentials-file section and the environment variable behind each of its keys.
_CREDENTIAL_ENV = {
    "linear": ("linear", {"LINEAR_API_KEY": "api_key"}),
    "jira": ("atlassian", {"ATLASSIAN_SERVICE_ACCOUNT_KEY": "service_account_key", "ATLASSIAN_EMAIL": "email",
                           "ATLASSIAN_API_TOKEN": "api_token"}),
}


def _complete(values: dict[str, str]) -> bool:
    return bool(values.get("api_key") or values.get("service_account_key")
                or (values.get("email") and values.get("api_token")))


@contextmanager
def credentials_for_agent_work_commands(backend: str) -> Iterator[None]:
    """Remove tracker credentials from the environment an interactive agent inherits, yet let the
    `ae-sdlc work` commands it runs reach the tracker.

    Credentials found in the environment move to a temporary mode-600 file for the length of the run,
    and AE_SDLC_CREDENTIALS_FILE names it, so the agent's environment holds a path and no secret.
    Without them in the environment, the user's credentials file applies as before.
    """
    section, names = _CREDENTIAL_ENV.get(backend, ("", {}))
    values = {key: os.environ[name] for name, key in names.items() if os.environ.get(name)}
    remove_tracker_credentials_from_env()
    if not _complete(values):
        yield
        return
    previous = os.environ.get("AE_SDLC_CREDENTIALS_FILE")
    directory = Path(tempfile.mkdtemp(prefix="ae-sdlc-credentials-"))
    try:
        path = directory / "credentials.toml"
        atomic_write(path, render_toml({section: values}), 0o600)
        os.environ["AE_SDLC_CREDENTIALS_FILE"] = str(path)
        yield
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        if previous is None:
            os.environ.pop("AE_SDLC_CREDENTIALS_FILE", None)
        else:
            os.environ["AE_SDLC_CREDENTIALS_FILE"] = previous


def installed() -> list[str]:
    try:
        return list(json.loads(MANIFEST.read_text(encoding="utf-8")).get("providers", []))
    except Exception:
        return []


def resolve(name: str | None = None) -> str:
    """--provider flag > $AE_SDLC_PROVIDER > the only installed provider."""
    name = name or os.environ.get("AE_SDLC_PROVIDER")
    have = installed()
    if name:
        if have and name not in have:
            raise SystemExit(f"provider '{name}' is not installed here (installed: {', '.join(have)})")
        return name
    if len(have) == 1:
        return have[0]
    raise SystemExit(
        "several providers installed; choose one with --provider or $AE_SDLC_PROVIDER "
        f"(installed: {', '.join(have) or 'none'})"
    )


def load(name: str):
    return importlib.import_module(f"providers.{name}")
