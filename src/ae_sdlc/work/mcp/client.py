"""A synchronous, reusable MCP client over Streamable HTTP.

The SDK's connection objects must be entered and exited by one task, while callers are synchronous
and make many calls per session. One owner task therefore holds the session and executes queued
jobs; asyncio.Runner keeps that task alive between calls.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, PaginatedRequestParams

from ..backend import WorkConfigError
from .auth import Credential
from .errors import (InvalidResponse, McpFailure, RateLimited, TransportFailure, failure_from_http,
                     failure_from_tool, parse_payload, redact)

log = logging.getLogger("ae_sdlc.mcp")
T = TypeVar("T")

ENDPOINTS = {"linear": "https://mcp.linear.app/mcp", "jira": "https://mcp.atlassian.com/v2/mcp"}
_MAX_RETRY_AFTER = 60.0
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def provider_endpoint(provider: str, env: Mapping[str, str] = os.environ) -> str:
    """Return the endpoint. The override is environment-only: a repository file must not be able
    to redirect a user's credential to another host."""
    url = env.get(f"AE_SDLC_{provider.upper()}_MCP_URL") or ENDPOINTS[provider]
    parts = urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOOPBACK):
        raise WorkConfigError(f"refusing MCP endpoint {parts.scheme}://{parts.hostname}: https is required")
    return url


@dataclass(frozen=True)
class ToolResponse:
    text: str
    data: Any  # the decoded JSON, else structured content; None for plain text

    def require(self, kind: type[T], tool: str) -> T:
        if not isinstance(self.data, kind):
            raise InvalidResponse(f"{tool}: expected a {kind.__name__} response, got {type(self.data).__name__}")
        return self.data


def _leaves(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None  # the HTTP-date form; the normal backoff applies


class McpClient:
    def __init__(self, url: str, credential: Credential, *, timeout: float = 30.0, retries: int = 3,
                 backoff: float = 0.5, sleep: Callable[[float], None] = time.sleep):
        self.url = url
        self._credential = credential
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep
        self._runner: asyncio.Runner | None = None
        self._owner: asyncio.Task | None = None
        self._jobs: asyncio.Queue | None = None
        self._broken: McpFailure | None = None
        self._http_status: int | None = None
        self._http_retry_after: float | None = None

    def __enter__(self) -> "McpClient":
        self._with_retries(None, retry_safe=True)
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._runner is None:
            return
        try:
            self._runner.run(self._stop())
        finally:
            self._runner.close()
            self._runner = self._owner = self._jobs = None

    def list_tools(self) -> list[Any]:
        async def fetch(session: ClientSession) -> list[Any]:
            tools, cursor = [], None
            while True:
                page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                tools += page.tools
                cursor = page.next_cursor
                if not cursor:
                    return tools
        return self._with_retries(fetch, retry_safe=True)

    def call(self, tool: str, arguments: dict[str, Any], *, retry_safe: bool = False) -> ToolResponse:
        """Invoke a tool. Set retry_safe only for reads: after a transport failure a write may
        already have been applied. A rate-limited request was refused, so it is always retried."""
        async def invoke(session: ClientSession) -> ToolResponse:
            started = time.monotonic()
            result = await session.call_tool(tool, arguments)
            log.debug("mcp call %s finished in %.2fs", tool, time.monotonic() - started)
            return self._to_response(tool, result)
        return self._with_retries(invoke, retry_safe=retry_safe)

    def _to_response(self, tool: str, result: Any) -> ToolResponse:
        if not isinstance(result, CallToolResult):
            raise InvalidResponse(f"{tool}: the server asked for input instead of returning a result")
        text = "\n".join(block.text for block in result.content if getattr(block, "type", None) == "text")
        failure = failure_from_tool(tool, text, bool(result.is_error))
        if failure is not None:
            raise failure
        payload = parse_payload(text)
        return ToolResponse(text, result.structured_content if payload is None else payload)

    def _with_retries(self, job: Callable[[ClientSession], Awaitable[T]] | None, *, retry_safe: bool) -> T:
        attempt = 0
        while True:
            try:
                self._ensure_connected()
                return self._run(job) if job else None  # type: ignore[return-value]
            except RateLimited as exc:
                if attempt >= self._retries:
                    raise
                delay = exc.retry_after
            except TransportFailure:
                if not retry_safe or attempt >= self._retries:
                    raise
                delay = None
            wait = self._backoff * 2 ** attempt if delay is None else min(delay, _MAX_RETRY_AFTER)
            log.debug("mcp request retried after %.2fs", wait)
            self._sleep(wait)
            attempt += 1

    def _ensure_connected(self) -> None:
        if self._owner is not None and not self._owner.done():
            return
        self.close()
        self._runner = asyncio.Runner()
        self._broken = None
        try:
            self._runner.run(self._start())
        except BaseException:
            self._runner.close()
            self._runner = self._owner = self._jobs = None
            raise

    def _run(self, job: Callable[[ClientSession], Awaitable[T]]) -> T:
        self._http_status = self._http_retry_after = None
        try:
            return self._runner.run(self._dispatch(job))
        except Exception as exc:
            raise self._classify(exc) from None

    async def _start(self) -> None:
        self._jobs = asyncio.Queue()
        ready = asyncio.get_running_loop().create_future()
        self._owner = asyncio.create_task(self._serve(ready))
        await ready

    async def _stop(self) -> None:
        if self._owner is not None and not self._owner.done():
            self._jobs.put_nowait(None)
            await asyncio.wait({self._owner}, timeout=5)

    async def _dispatch(self, job: Callable[[ClientSession], Awaitable[T]]) -> T:
        future = asyncio.get_running_loop().create_future()
        self._jobs.put_nowait((job, future))
        await asyncio.wait({future, self._owner}, return_when=asyncio.FIRST_COMPLETED)
        if future.done():
            return future.result()
        raise self._broken or TransportFailure("the MCP connection closed unexpectedly")

    async def _note_response(self, response: httpx2.Response) -> None:
        # The SDK reports every HTTP failure as one generic error, so the status is captured here.
        # Only POST carries requests; a 405 on the optional GET stream is normal.
        if response.status_code >= 400 and response.request.method == "POST":
            self._http_status = response.status_code
            self._http_retry_after = _retry_after(response.headers.get("retry-after"))

    async def _serve(self, ready: asyncio.Future) -> None:
        try:
            http = httpx2.AsyncClient(headers={"Authorization": self._credential.authorization},
                                      timeout=self._timeout, event_hooks={"response": [self._note_response]})
            async with http, streamable_http_client(self.url, http_client=http) as (read, write, *_):
                async with ClientSession(read, write, read_timeout_seconds=self._timeout) as session:
                    await session.initialize()
                    ready.set_result(None)
                    while (item := await self._jobs.get()) is not None:
                        job, future = item
                        try:
                            future.set_result(await job(session))
                        except Exception as exc:
                            future.set_exception(exc)
        except Exception as exc:
            failure = self._classify(exc)
            if ready.done():
                self._broken = failure
            else:
                ready.set_exception(failure)

    def _classify(self, exc: BaseException) -> McpFailure:
        secrets = self._credential.secrets
        if isinstance(exc, McpFailure):
            exc.args = tuple(redact(str(a), secrets) for a in exc.args)
            return exc
        if self._http_status is not None:
            return failure_from_http(self._http_status, self._http_retry_after)
        leaves = list(_leaves(exc))
        host = urlsplit(self.url).hostname
        for leaf in leaves:
            if isinstance(leaf, (httpx2.TransportError, TimeoutError, OSError)):
                return TransportFailure(f"cannot reach {host}: {type(leaf).__name__}")
        for leaf in leaves:
            if isinstance(leaf, MCPError):
                message = redact(str(leaf), secrets)
                if leaf.error.code == 408 or "timed out" in message.lower():
                    return TransportFailure(f"{host} did not answer within {self._timeout:g}s")
                return InvalidResponse(f"MCP protocol error from {host}: {message}")
        return TransportFailure(redact(f"MCP connection to {host} failed: {type(leaves[0]).__name__}", secrets))
