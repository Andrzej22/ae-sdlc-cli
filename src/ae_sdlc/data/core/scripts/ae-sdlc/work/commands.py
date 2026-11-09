"""The `./ae-sdlc work` commands: JSON on stdout, a meaningful exit code, no progress text.

Exit codes: 0 ok, 1 backend error, 2 invalid input, 3 not found, 4 conflict, 5 invalid transition,
6 configuration or missing capability, 7 authentication or permission, 8 temporarily unavailable.
A failure prints {"ok": false, "error": {"code": ..., "message": ...}}.

Payloads are read from stdin or a file and never from an argument, so a complete spec fits. The file
form exists for agent sandboxes whose per-command rules do not match a command with a redirection.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, ContextManager, NoReturn

from .backend import InvalidInput, WorkBackendError
from .models import WorkItem
from .publish import publish
from .service import WorkService

DEFAULT_COMMENT_LIMIT = 20
_SUMMARY_FIELDS = ("id", "kind", "title", "status", "parent_id", "dependencies", "blocker", "revision", "url")


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


class _JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _emit({"ok": False, "error": {"code": InvalidInput.code, "message": f"{self.prog}: {message}"}})
        raise SystemExit(InvalidInput.exit_code)


def _input_options(parser: argparse.ArgumentParser, name: str) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(f"--{name}-stdin", dest="from_stdin", action="store_true")
    group.add_argument(f"--{name}-file", dest="from_file", metavar="PATH")


def _parser() -> argparse.ArgumentParser:
    parser = _JsonParser(prog="ae-sdlc work", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="work_cmd", required=True)

    sub.add_parser("get", help="print one item in full").add_argument("id")

    p = sub.add_parser("list", help="print the specs of a spec or epic")
    p.add_argument("scope")
    p.add_argument("--status", choices=["DRAFT", "READY", "IN_PROGRESS", "DONE", "BLOCKED"])
    p.add_argument("--eligible", action="store_true", help="only specs whose dependencies are DONE")

    p = sub.add_parser("lookup", help="search existing work by text")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)

    _input_options(sub.add_parser("publish", help="create or update an epic and specs"), "json")

    p = sub.add_parser("context", help="print what implementing a spec needs, changing nothing")
    p.add_argument("id")
    p.add_argument("--comments", type=int, default=DEFAULT_COMMENT_LIMIT)

    p = sub.add_parser("start", help="validate a READY spec, mark it IN_PROGRESS, print its context")
    p.add_argument("id")
    p.add_argument("--comments", type=int, default=DEFAULT_COMMENT_LIMIT)

    p = sub.add_parser("finish", help="apply an execution result to a started spec")
    p.add_argument("id")
    _input_options(p, "result")

    p = sub.add_parser("comment", help="add a comment to an item")
    p.add_argument("id")
    p.add_argument("--dedupe-key", help="a repeat with the same key adds nothing")
    _input_options(p, "text")
    return parser


def parse(argv: list[str]) -> argparse.Namespace:
    return _parser().parse_args(argv)


def _summary(item: WorkItem) -> dict:
    return {key: getattr(item, key) for key in _SUMMARY_FIELDS}


def _context(context: Any) -> dict:
    return {
        "item": asdict(context.item),
        "epic": asdict(context.parent) if context.parent else None,
        "dependencies": [_summary(d) for d in context.dependencies],
        "related": [_summary(r) for r in context.related],
        "comments": [asdict(c) for c in context.comments],
    }


def _get(service: WorkService, args: argparse.Namespace, _: Any) -> dict:
    return {"item": asdict(service.backend.get(args.id))}


def _list(service: WorkService, args: argparse.Namespace, _: Any) -> dict:
    return {"items": [_summary(i) for i in service.list_scope(args.scope, args.status, args.eligible)]}


def _lookup(service: WorkService, args: argparse.Namespace, _: Any) -> dict:
    return {"items": [_summary(i) for i in service.backend.search(args.query, args.limit)]}


def _publish(service: WorkService, _: argparse.Namespace, data: Any) -> dict:
    return publish(service.backend, data)


def _show_context(service: WorkService, args: argparse.Namespace, _: Any) -> dict:
    return _context(service.prepare_execution_context(args.id, args.comments))


def _start(service: WorkService, args: argparse.Namespace, _: Any) -> dict:
    context, resumed = service.begin_execution(args.id, args.comments)
    return {**_context(context), "resumed": resumed}


def _finish(service: WorkService, args: argparse.Namespace, data: Any) -> dict:
    item, finished, repeated = service.complete_execution(args.id, data)
    return {"id": item.id, "status": item.status, "url": item.url, "disposition": finished.disposition,
            "detail": finished.detail, "repeated": repeated}


def _comment(service: WorkService, args: argparse.Namespace, data: Any) -> dict:
    if not data.strip():
        raise InvalidInput("the comment text is empty")
    return {"comment": asdict(service.backend.add_comment(args.id, data, args.dedupe_key))}


_HANDLERS: dict[str, Callable[[WorkService, argparse.Namespace, Any], dict]] = {
    "get": _get, "list": _list, "lookup": _lookup, "publish": _publish, "context": _show_context,
    "start": _start, "finish": _finish, "comment": _comment,
}


def _read_input(args: argparse.Namespace) -> Any:
    """Return the command's payload. Reading it first means bad input never reaches a backend."""
    if args.work_cmd not in {"publish", "finish", "comment"}:
        return None
    try:
        raw = Path(args.from_file).read_text(encoding="utf-8") if args.from_file else sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidInput(f"cannot read the input: {exc}") from exc
    if args.work_cmd == "comment":
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"the input is not valid JSON: {exc}") from exc


def fail(exc: WorkBackendError) -> int:
    payload: dict[str, Any] = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    if partial := getattr(exc, "partial", None):
        payload["partial"] = partial
    _emit(payload)
    return exc.exit_code


def execute(args: argparse.Namespace, open_service: Callable[[], ContextManager[WorkService]]) -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        data = _read_input(args)
        with open_service() as service:
            payload = _HANDLERS[args.work_cmd](service, args, data)
    except WorkBackendError as exc:
        return fail(exc)
    _emit({"ok": True, **payload})
    return 0
