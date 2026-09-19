#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers

errors: list[str] = []
warnings: list[str] = []

SKILLS = ["prepare-work", "execute-work", "run-workflow", "work-status"]
required = [
    "AGENTS.md", "ae-sdlc",
    ".ae-sdlc/manifest.json",
    ".ae-sdlc/artifact-store.json",
    ".ae-sdlc/schemas/prepare-result.schema.json",
    ".ae-sdlc/schemas/execute-result.schema.json",
    ".ae-sdlc/workflows/nightly.toml",
    "scripts/ae-sdlc/work_store.py",
    "scripts/ae-sdlc/run_workflow.py",
    "scripts/ae-sdlc/validate_harness.py",
    "scripts/ae-sdlc/providers/__init__.py",
    "docs/ae-sdlc/ARCHITECTURE.md",
    "docs/ae-sdlc/ARTIFACT_STORE.md",
    "docs/product/README.md",
]
json_files = [
    ".ae-sdlc/manifest.json",
    ".ae-sdlc/artifact-store.json",
    ".ae-sdlc/schemas/prepare-result.schema.json",
    ".ae-sdlc/schemas/execute-result.schema.json",
]
# Thin-runtime invariants: prevent accidental reintroduction of expensive machinery.
forbidden = [
    ".ae-sdlc/quality-policy.json",
    ".ae-sdlc/quality-gates.json",
    "scripts/ae-sdlc/run_quality_gates.py",
    "scripts/ae-sdlc/artifact_store.py",
]

names = providers.installed()
if not names:
    errors.append("no providers recorded in .ae-sdlc/manifest.json")

adapters = []
for name in names:
    try:
        adapters.append(providers.load(name))
    except Exception as exc:
        errors.append(f"cannot load provider adapter '{name}': {exc}")

for a in adapters:
    required += a.REQUIRED
    json_files += a.JSON_FILES
    forbidden += a.FORBIDDEN

for rel in required:
    if not (R / rel).exists():
        errors.append(f"missing: {rel}")

for a in adapters:
    for name in SKILLS:
        p = R / a.skill_path(name)
        if not p.exists():
            errors.append(f"[{a.NAME}] missing skill/command: {name}")
            continue
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "description:" not in text:
            errors.append(f"[{a.NAME}] invalid frontmatter: {name}")
        elif a.NAME != "opencode" and f"name: {name}" not in text:
            errors.append(f"[{a.NAME}] invalid skill frontmatter: {name}")

    p = R / a.skill_path("execute-work")
    if p.exists():
        t = p.read_text(encoding="utf-8")
        if "do not call" not in t.lower() or "scripts/ae-sdlc/*" not in t:
            warnings.append(f"[{a.NAME}] execute-work should explicitly forbid harness helper-script calls")
    a.extra_checks(R, errors, warnings)

for p in [R / "ae-sdlc", *sorted((R / "scripts" / "ae-sdlc").rglob("*.py"))]:
    if p.exists():
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except Exception as exc:
            errors.append(f"python syntax: {p.relative_to(R)}: {exc}")

for rel in json_files:
    try:
        json.loads((R / rel).read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid JSON {rel}: {exc}")

for p in (R / ".ae-sdlc" / "workflows").glob("*.toml"):
    try:
        cfg = tomllib.loads(p.read_text(encoding="utf-8"))
        if int(cfg.get("parallelism", 1)) != 1:
            warnings.append(f"{p.name}: bundled runner only supports parallelism=1")
    except Exception as exc:
        errors.append(f"invalid TOML {p.relative_to(R)}: {exc}")

for rel in forbidden:
    if (R / rel).exists():
        errors.append(f"thin-runtime forbidden file present: {rel}")

print("Thin command-driven AE-SDLC validation")
for w in warnings:
    print("WARNING:", w)
for e in errors:
    print("ERROR:", e)
print(("FAILED" if errors else "OK") + f": {len(errors)} error(s), {len(warnings)} warning(s)")
raise SystemExit(1 if errors else 0)
