"""Plan and apply an install of the harness into a target project.

Layers live under ``data/``: ``core`` (always), and one overlay per provider. Each overlay is a
directory tree mirrored into the project root. The codex and agy providers share ``_agents``
(the ``.agents/skills`` tree) so it is installed only once.

Conflict policy: an existing file with different content is never modified; the harness version
is written next to it as ``<file>.ae_sdlc`` and reported.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

DATA = Path(__file__).resolve().parent / "data"
SIDECAR = ".ae_sdlc"

SKILLS = ["prepare-work", "execute-work", "run-workflow", "work-status"]

# provider -> (overlay directories under data/providers, invocation syntax, human label)
PROVIDERS: dict[str, tuple[list[str], str, str]] = {
    "claude": (["claude"], "/{}", "Claude Code"),
    "codex": (["_agents", "codex"], "${}", "Codex"),
    "cursor": (["cursor"], "/{}", "Cursor"),
    "opencode": (["opencode"], "/{}", "opencode"),
    "agy": (["_agents", "agy"], "/{}", "Antigravity"),
}

# core files whose packaged name differs from the installed name
RENAMES = {"gitignore": ".gitignore"}
SKIP_NAMES = {"AGENTS.md.tmpl", "__pycache__"}
EXECUTABLE = {"ai-sdlc"}


@dataclass
class Plan:
    files: dict[str, tuple[bytes, bool]] = field(default_factory=dict)  # rel path -> (content, executable)


@dataclass
class Result:
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # sidecar paths written


def _walk(overlay: Path):
    for p in sorted(overlay.rglob("*")):
        if p.is_file() and not (set(p.relative_to(overlay).parts) & SKIP_NAMES) and p.suffix != ".pyc":
            yield p.relative_to(overlay).as_posix(), p


def _add(plan: Plan, rel: str, data: bytes, executable: bool = False) -> None:
    prev = plan.files.get(rel)
    if prev is not None and prev[0] != data:
        raise RuntimeError(f"internal error: overlays disagree on {rel}")
    plan.files[rel] = (data, executable)


def render_agents_md(providers: list[str]) -> str:
    rows = "\n".join(
        f"- {PROVIDERS[p][2]}: " + ", ".join(f"`{PROVIDERS[p][1].format(s)}`" for s in SKILLS)
        for p in providers
    )
    tmpl = (DATA / "core" / "AGENTS.md.tmpl").read_text(encoding="utf-8")
    return tmpl.replace("{{INVOCATIONS}}", "Invoke them per agent:\n\n" + rows)


def existing_providers(target: Path) -> list[str]:
    try:
        data = json.loads((target / ".ai-sdlc" / "manifest.json").read_text(encoding="utf-8"))
        return [p for p in data.get("providers", []) if p in PROVIDERS] if data.get("name") == "ae-sdlc" else []
    except Exception:
        return []


def build_plan(providers: list[str], target: Path) -> Plan:
    # Re-running init to add a provider keeps the previously installed ones in the manifest.
    all_providers = [p for p in PROVIDERS if p in set(providers) | set(existing_providers(target))]
    plan = Plan()

    for rel, path in _walk(DATA / "core"):
        rel = RENAMES.get(rel, rel)
        _add(plan, rel, path.read_bytes(), rel in EXECUTABLE)

    overlays: list[str] = []
    for p in providers:
        for o in PROVIDERS[p][0]:
            if o not in overlays:
                overlays.append(o)
    for o in overlays:
        for rel, path in _walk(DATA / "providers" / o):
            _add(plan, rel, path.read_bytes())

    agents = render_agents_md(all_providers).encode()
    _add(plan, "AGENTS.md", agents)
    if "claude" in providers:
        _add(plan, "CLAUDE.md", b"@AGENTS.md\n")
    if "agy" in providers:
        _add(plan, "GEMINI.md", agents)

    manifest = {
        "name": "ae-sdlc",
        "version": __version__,
        "providers": all_providers,
        "artifact_store": {"product": "docs/product", "project_memory": "docs/project-memory"},
        "workflow_runner": "scripts/ai-sdlc/run_workflow.py",
        "shell_entrypoint": "./ai-sdlc",
    }
    _add(plan, ".ai-sdlc/manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    return plan


def apply(plan: Plan, target: Path, dry_run: bool = False) -> Result:
    res = Result()
    for rel, (data, executable) in sorted(plan.files.items()):
        dest = target / rel
        if dest.exists():
            if dest.read_bytes() == data:
                res.unchanged.append(rel)
                continue
            if rel == ".ai-sdlc/manifest.json" and _is_ours(dest):
                pass  # our own manifest: update in place (provider list is merged in build_plan)
            else:
                dest = dest.with_name(dest.name + SIDECAR)
                rel = rel + SIDECAR
                if dest.exists() and dest.read_bytes() == data:
                    res.conflicts.append(rel)
                    continue
                res.conflicts.append(rel)
        else:
            res.created.append(rel)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if executable:
                dest.chmod(dest.stat().st_mode | 0o111)
    return res


def _is_ours(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("name") == "ae-sdlc"
    except Exception:
        return False
