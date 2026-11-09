"""Classified MCP failures, and the classification of provider responses into them.

Failures reach us in three places: HTTP status (the SDK hides it, so the client captures it), a tool
result with is_error set, and a payload with is_error unset that still reports an error. Jira uses
the last form for a failed transition, so is_error alone is not trustworthy.
"""
from __future__ import annotations

import json
from typing import Any

from ..backend import WorkBackendError


class McpFailure(WorkBackendError):
    """Base class. Messages are safe to show: they never contain credentials."""


class CredentialsMissing(McpFailure):
    pass


class AuthRejected(McpFailure):
    """The server did not accept the credential."""


class PermissionDenied(McpFailure):
    """The credential is valid but may not do this."""


class RateLimited(McpFailure):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TransportFailure(McpFailure):
    """Network failure, timeout or server-side error. The request may or may not have been applied."""


class ToolFailed(McpFailure):
    """The tool ran and reported a failure, such as an invalid status or an unknown issue."""


class InvalidResponse(McpFailure):
    """The server answered with something the protocol or the caller cannot use."""


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def parse_payload(text: str) -> Any:
    """Decode the JSON object or array at the start of a tool response; None for plain text.

    A trailing part is ignored because Atlassian's discover appends plain text after its JSON.
    Scalars are rejected so that text such as "5 issues found" is not read as the number 5.
    """
    try:
        value = json.JSONDecoder().raw_decode(text.lstrip())[0]
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


def _status_of(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("statusCode", "status"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 400:
            return value
    return None


def failure_from_http(status: int, retry_after: float | None = None) -> McpFailure:
    if status == 401:
        return AuthRejected("the server rejected the credential (HTTP 401); check that it is correct and not expired")
    if status == 403:
        return PermissionDenied("the credential is not allowed to use this MCP server (HTTP 403)")
    if status == 429:
        return RateLimited("rate limited by the server (HTTP 429)", retry_after)
    if status >= 500:
        return TransportFailure(f"the server failed (HTTP {status})")
    return TransportFailure(f"unexpected HTTP {status} from the server; check the endpoint")


def failure_from_tool(tool: str, text: str, is_error: bool) -> McpFailure | None:
    """Return the failure a tool response reports, or None for a successful response."""
    payload = parse_payload(text)
    status = _status_of(payload)
    if not is_error and status is None:
        return None

    lowered = text.lower()
    if "permission to connect via api token" in lowered:
        return PermissionDenied("API-token access to the Atlassian MCP server is not enabled for this organization; "
                                "an organization admin must enable it")
    if "missing the scope claim" in lowered:
        return PermissionDenied("the Atlassian API token has no scopes; create a scoped token that includes Jira access")
    if status == 401:
        return AuthRejected(f"{tool}: the server rejected the credential; check that it is correct and not expired")
    if status == 403:
        return PermissionDenied(f"{tool}: the credential lacks permission for this operation")
    if status == 429:
        return RateLimited(f"{tool}: rate limited by the server")
    if status is not None and status >= 500:
        return TransportFailure(f"{tool}: the provider failed (status {status})")

    detail = payload.get("message") or payload.get("error") if isinstance(payload, dict) else None
    return ToolFailed(f"{tool}: {detail or text.strip()[:500] or 'the tool reported an error'}")
