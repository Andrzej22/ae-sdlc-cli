#!/usr/bin/env python3
"""PR 0 feasibility probe (NOT shipped in the wheel, NOT part of the runtime).

Connects to a remote MCP server over Streamable HTTP with credentials taken from the environment,
and records the tool catalog actually visible to that identity. Never prints or stores the token.

    LINEAR_API_KEY=... uv run --no-project --with mcp==2.3.0 tools/mcp_probe.py linear
    ATLASSIAN_EMAIL=... ATLASSIAN_API_TOKEN=... uv run --no-project --with mcp==2.3.0 tools/mcp_probe.py jira [--all]
    ATLASSIAN_SERVICE_ACCOUNT_KEY=... uv run --no-project --with mcp==2.3.0 tools/mcp_probe.py jira [--all]

Writes <out>/<provider>-tools.json (names, descriptions, input schemas) and prints a summary.
Exit codes: 0 ok, 2 missing credential, 3 auth rejected (401/403), 4 transport/other error.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys

ENDPOINTS = {
    "linear": ("https://mcp.linear.app/mcp",),
    "jira": ("https://mcp.atlassian.com/v2/mcp",),
}


def auth_header(provider: str) -> tuple[str, list[str]] | None:
    """Return (Authorization value, secrets to redact), or None when credentials are missing."""
    env = os.environ
    if provider == "linear":
        key = env.get("LINEAR_API_KEY")
        return (f"Bearer {key}", [key]) if key else None
    # Atlassian: service-account key => Bearer; personal API token => Basic base64(email:token).
    if key := env.get("ATLASSIAN_SERVICE_ACCOUNT_KEY"):
        return f"Bearer {key}", [key]
    email, token = env.get("ATLASSIAN_EMAIL"), env.get("ATLASSIAN_API_TOKEN")
    if email and token:
        encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
        return f"Basic {encoded}", [token, encoded]
    return None


def _leaves(exc: BaseException):
    """Flatten anyio/ExceptionGroup nesting so the real cause (e.g. HTTP 401) is visible."""
    if isinstance(exc, BaseExceptionGroup):
        for e in exc.exceptions:
            yield from _leaves(e)
    else:
        yield exc


async def probe(provider: str, flat: bool, out: str) -> int:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import PaginatedRequestParams

    url = ENDPOINTS[provider][0]
    auth = auth_header(provider)
    if auth is None:
        need = "LINEAR_API_KEY" if provider == "linear" else \
            "ATLASSIAN_SERVICE_ACCOUNT_KEY, or ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN"
        print(f"missing credential: set {need}", file=sys.stderr)
        return 2
    header, secrets = auth
    if provider == "jira" and flat:
        url += "?tools=all"
    seen: list[int] = []

    async def note(resp):  # SDK 2.x flattens HTTP errors to JSON-RPC -32603; keep the real status
        if resp.status_code >= 400:
            seen.append(resp.status_code)

    client = httpx2.AsyncClient(headers={"Authorization": header}, timeout=30,
                                event_hooks={"response": [note]})
    try:
        async with client, streamable_http_client(url, http_client=client) as (r, w, *_):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools, cursor = [], None
                while True:
                    page = await s.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                    tools += page.tools
                    cursor = page.next_cursor
                    if not cursor:
                        break
    except Exception as exc:  # classify without leaking headers
        msg = " | ".join(f"{type(e).__name__}: {e} {getattr(e, 'error', '')!r}" for e in _leaves(exc))
        for secret in secrets:
            msg = msg.replace(secret, "***")
        print(f"error: {msg} http_status={seen or None}", file=sys.stderr)
        return 3 if any(c in (401, 403) for c in seen) else 4
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"{provider}-tools.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"server": init.server_info.name if init.server_info else None,
                   "protocol": init.protocol_version,
                   "tools": [{"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                             for t in tools]}, f, indent=2)
    print(f"{provider}: {len(tools)} tools -> {path}")
    for t in tools:
        print(" ", t.name)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=ENDPOINTS)
    ap.add_argument("--all", action="store_true", help="jira: request the flat paginated tool list")
    ap.add_argument("--out", default=".ae-sdlc-runtime/probe")
    a = ap.parse_args()
    return asyncio.run(probe(a.provider, a.all, a.out))


if __name__ == "__main__":
    raise SystemExit(main())
