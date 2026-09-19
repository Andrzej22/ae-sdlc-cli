#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    """Install root: the directory that holds scripts/ae-sdlc/ (independent of cwd)."""
    return Path(__file__).resolve().parents[2]


ROOT = repo_root()
PRODUCT = ROOT / "docs" / "product"
VALID_STATUSES = {"DRAFT", "READY", "IN_PROGRESS", "DONE", "BLOCKED"}


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw == "":
        return None
    if raw in {"null", "~"}:
        return None
    if raw.startswith("[") and raw.endswith("]"):
        try:
            return json.loads(raw)
        except Exception:
            return []
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = _scalar(v)
    return meta, text[end + 5:]


def render_frontmatter(meta: dict[str, Any], body: str) -> str:
    preferred = [
        "artifact_type", "work_item_id", "title", "status", "epic",
        "source_of_truth", "dependencies", "blocker",
    ]
    keys = preferred + [k for k in meta if k not in preferred]
    lines = ["---"]
    for k in keys:
        if k not in meta:
            continue
        v = meta[k]
        if isinstance(v, list):
            s = json.dumps(v, ensure_ascii=False)
        elif v is None:
            s = ""
        else:
            s = str(v)
        lines.append(f"{k}: {s}")
    lines.extend(["---", "", body.lstrip()])
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class Record:
    path: Path
    meta: dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("work_item_id") or "")

    @property
    def type(self) -> str:
        return str(self.meta.get("artifact_type") or "")

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or "")


class WorkStore:
    """One in-process snapshot of epic/spec metadata."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self.records: list[Record] = []
        self.by_id: dict[str, Record] = {}
        self.scan_once()

    def scan_once(self) -> None:
        self.records.clear()
        self.by_id.clear()
        product = self.root / "docs" / "product"
        if not product.exists():
            return
        for p in sorted(product.rglob("*.md")):
            if "_templates" in p.parts or p.name == "README.md":
                continue
            parsed = parse_frontmatter(p)
            if not parsed:
                continue
            meta, body = parsed
            if meta.get("artifact_type") not in {"epic", "spec"}:
                continue
            rec = Record(p, meta, body)
            self.records.append(rec)
            if rec.id:
                self.by_id[rec.id] = rec

    def get(self, work_id: str) -> Record | None:
        return self.by_id.get(work_id)

    def specs_for_scope(self, scope: str, status: str | None = None) -> list[Record]:
        direct = self.get(scope)
        if direct and direct.type == "spec":
            specs = [direct]
        elif direct and direct.type == "epic":
            specs = [r for r in self.records if r.type == "spec" and r.meta.get("epic") == scope]
        else:
            candidate = self.root / scope
            specs = []
            if candidate.exists() and candidate.is_file():
                parsed = parse_frontmatter(candidate)
                if parsed and parsed[0].get("artifact_type") == "spec":
                    specs = [Record(candidate, parsed[0], parsed[1])]
        if status is not None:
            specs = [r for r in specs if r.status == status]
        return sorted(specs, key=lambda r: r.id)

    def state_map(self) -> dict[str, str]:
        return {r.id: r.status for r in self.records if r.id}

    def eligible_specs(self, scope: str, status: str = "READY") -> list[Record]:
        states = self.state_map()
        result = []
        for rec in self.specs_for_scope(scope, status=status):
            deps = rec.meta.get("dependencies") or []
            if not isinstance(deps, list):
                deps = []
            if all(states.get(str(d)) == "DONE" for d in deps):
                result.append(rec)
        return result

    def set_status(self, work_id: str, status: str, blocker: str | None = None) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        rec = self.get(work_id)
        if not rec:
            raise KeyError(work_id)
        rec.meta["status"] = status
        if blocker:
            rec.meta["blocker"] = blocker.replace("\n", " ").strip()
        elif "blocker" in rec.meta:
            rec.meta.pop("blocker", None)
        rec.path.write_text(render_frontmatter(rec.meta, rec.body), encoding="utf-8")

    def summary_lines(self, scope: str | None = None, only_status: str | None = None) -> list[str]:
        rows: list[Record]
        if scope:
            direct = self.get(scope)
            if direct and direct.type == "epic":
                rows = [direct] + self.specs_for_scope(scope)
            elif direct:
                rows = [direct]
            else:
                rows = []
        else:
            rows = list(self.records)
        if only_status:
            rows = [r for r in rows if r.status == only_status]
        if not rows:
            return ["No matching durable work found."]
        lines = []
        for status in ["DRAFT", "READY", "IN_PROGRESS", "BLOCKED", "DONE"]:
            group = [r for r in rows if r.status == status]
            if not group:
                continue
            lines.append(status)
            for r in group:
                extra = ""
                deps = r.meta.get("dependencies") or []
                if deps:
                    extra += f" deps={deps}"
                if r.meta.get("blocker"):
                    extra += f" blocker={r.meta.get('blocker')}"
                lines.append(f"  {r.id} [{r.type}] {r.meta.get('title')}{extra}")
        return lines
