"""A real Streamable HTTP MCP server on localhost, so tests exercise the actual SDK transport."""
from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import uvicorn
from mcp.server import MCPServer
from starlette.responses import JSONResponse

GOOD_AUTH = "Bearer good-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Controls:
    """Mutable behavior for a running server."""

    def __init__(self):
        self.http_failures: list[tuple[int, dict]] = []  # consumed one per tools/call POST
        self.seen_auth: list[str] = []
        self.calls: list[str] = []


def build_server(tools: dict[str, Callable[..., str]]) -> MCPServer:
    server = MCPServer("fake", log_level="ERROR")
    for name, fn in tools.items():
        server.add_tool(fn, name=name)
    return server


class _Gate:
    """Authentication and injected HTTP failures in front of the MCP app."""

    def __init__(self, app, controls: Controls):
        self.app, self.controls = app, controls

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        auth = dict(scope["headers"]).get(b"authorization", b"").decode()
        self.controls.seen_auth.append(auth)
        if auth != GOOD_AUTH:
            return await JSONResponse({"error": "invalid_token"}, 401)(scope, receive, send)
        chunks, more = [], True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)

        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()  # later reads wait for http.disconnect
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        method = json.loads(body).get("method") if body else None
        if method == "tools/call":
            self.controls.calls.append(json.loads(body)["params"]["name"])
            if self.controls.http_failures:
                status, headers = self.controls.http_failures.pop(0)
                return await JSONResponse({"error": "injected"}, status, headers)(scope, replay, send)
        return await self.app(scope, replay, send)


@contextmanager
def serve(tools: dict[str, Callable[..., str]]) -> Iterator[tuple[str, Controls]]:
    controls = Controls()
    port = _free_port()
    app = _Gate(build_server(tools).streamable_http_app(), controls)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("fake MCP server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/mcp", controls
    finally:
        server.should_exit = True
        thread.join(timeout=10)
