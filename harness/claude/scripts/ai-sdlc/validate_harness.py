#!/usr/bin/env python3
from __future__ import annotations

import json
import py_compile
import subprocess
import sys
import tomllib
from pathlib import Path


def root() -> Path:
    try:
        return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL).strip())
    except Exception:
        return Path.cwd()


R = root()
errors: list[str] = []
warnings: list[str] = []

required = [
    "CLAUDE.md", "README.md", "manifest.json", "ai-sdlc",
    ".claude/settings.json",
    ".ai-sdlc/artifact-store.json",
    ".ai-sdlc/schemas/prepare-result.schema.json",
    ".ai-sdlc/schemas/execute-result.schema.json",
    ".ai-sdlc/workflows/nightly.toml",
    "scripts/ai-sdlc/work_store.py",
    "scripts/ai-sdlc/run_workflow.py",
    "scripts/ai-sdlc/validate_harness.py",
    "docs/ai-sdlc/ARCHITECTURE.md",
    "docs/ai-sdlc/ARTIFACT_STORE.md",
    "docs/product/README.md",
]
for rel in required:
    if not (R / rel).exists():
        errors.append(f"missing: {rel}")

for name in ["prepare-work", "execute-work", "run-workflow", "work-status"]:
    p = R / ".claude" / "skills" / name / "SKILL.md"
    if not p.exists():
        errors.append(f"missing skill: {name}")
        continue
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---\n") or f"name: {name}" not in text or "description:" not in text:
        errors.append(f"invalid skill frontmatter: {name}")

for p in [R / "ai-sdlc", *sorted((R / "scripts" / "ai-sdlc").glob("*.py"))]:
    if p.exists():
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as exc:
            errors.append(f"python syntax: {p.relative_to(R)}: {exc}")

for rel in [
    ".ai-sdlc/artifact-store.json",
    ".ai-sdlc/schemas/prepare-result.schema.json",
    ".ai-sdlc/schemas/execute-result.schema.json",
    "manifest.json",
    ".claude/settings.json",
]:
    try:
        json.loads((R / rel).read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid JSON {rel}: {exc}")

for p in (R / ".ai-sdlc" / "workflows").glob("*.toml"):
    try:
        cfg = tomllib.loads(p.read_text(encoding="utf-8"))
        if int(cfg.get("parallelism", 1)) != 1:
            warnings.append(f"{p.name}: bundled runner only supports parallelism=1")
    except Exception as exc:
        errors.append(f"invalid TOML {p.relative_to(R)}: {exc}")

# Thin-runtime invariants: prevent accidental reintroduction of expensive machinery.
for forbidden in [
    ".ai-sdlc/quality-policy.json",
    ".ai-sdlc/quality-gates.json",
    "scripts/ai-sdlc/run_quality_gates.py",
    "scripts/ai-sdlc/artifact_store.py",
    ".claude/hooks",
    ".claude/agents",
    ".claude/commands",
]:
    if (R / forbidden).exists():
        errors.append(f"thin-runtime forbidden file present: {forbidden}")

settings = R / ".claude" / "settings.json"
if settings.exists():
    try:
        if "hooks" in json.loads(settings.read_text(encoding="utf-8")):
            errors.append("thin-runtime forbidden: hooks configured in .claude/settings.json")
    except Exception:
        pass

execute_skill = R / ".claude" / "skills" / "execute-work" / "SKILL.md"
if execute_skill.exists():
    t = execute_skill.read_text(encoding="utf-8")
    if "do not call" not in t.lower() or "scripts/ai-sdlc/*" not in t:
        warnings.append("execute-work should explicitly forbid harness helper-script calls")

print("Thin command-driven AI-SDLC validation")
for w in warnings:
    print("WARNING:", w)
for e in errors:
    print("ERROR:", e)
print(("FAILED" if errors else "OK") + f": {len(errors)} error(s), {len(warnings)} warning(s)")
raise SystemExit(1 if errors else 0)
