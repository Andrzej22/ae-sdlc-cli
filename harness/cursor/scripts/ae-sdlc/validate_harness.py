#!/usr/bin/env python3
from __future__ import annotations

import json
import py_compile
import subprocess
import sys
import tomllib
from pathlib import Path


def root() -> Path:
    if (Path.cwd() / "manifest.json").exists():
        return Path.cwd()
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / "manifest.json").exists():
        return candidate
    try:
        return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL).strip())
    except Exception:
        return Path.cwd()


R = root()
errors: list[str] = []
warnings: list[str] = []

required = [
    "AGENTS.md", "README.md", "manifest.json", "ae-sdlc",
    ".cursor/cli.json",
    ".cursor/rules/security.mdc",
    ".ae-sdlc/artifact-store.json",
    ".ae-sdlc/schemas/prepare-result.schema.json",
    ".ae-sdlc/schemas/execute-result.schema.json",
    ".ae-sdlc/workflows/nightly.toml",
    "scripts/ae-sdlc/work_store.py",
    "scripts/ae-sdlc/run_workflow.py",
    "scripts/ae-sdlc/validate_harness.py",
    "docs/ae-sdlc/ARCHITECTURE.md",
    "docs/ae-sdlc/ARTIFACT_STORE.md",
    "docs/product/README.md",
]
for rel in required:
    if not (R / rel).exists():
        errors.append(f"missing: {rel}")

for name in ["prepare-work", "execute-work", "run-workflow", "work-status"]:
    p = R / ".cursor" / "skills" / name / "SKILL.md"
    if not p.exists():
        errors.append(f"missing skill: {name}")
        continue
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---\n") or f"name: {name}" not in text or "description:" not in text:
        errors.append(f"invalid skill frontmatter: {name}")
    if "disable-model-invocation: true" not in text:
        errors.append(f"skill must set disable-model-invocation: true: {name}")

for p in [R / "ae-sdlc", *sorted((R / "scripts" / "ae-sdlc").glob("*.py"))]:
    if p.exists():
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as exc:
            errors.append(f"python syntax: {p.relative_to(R)}: {exc}")

for rel in [
    ".ae-sdlc/artifact-store.json",
    ".ae-sdlc/schemas/prepare-result.schema.json",
    ".ae-sdlc/schemas/execute-result.schema.json",
    "manifest.json",
    ".cursor/cli.json",
]:
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

# Thin-runtime invariants: prevent accidental reintroduction of expensive machinery.
for forbidden in [
    ".ae-sdlc/quality-policy.json",
    ".ae-sdlc/quality-gates.json",
    "scripts/ae-sdlc/run_quality_gates.py",
    "scripts/ae-sdlc/artifact_store.py",
    ".cursor/hooks.json",
    ".cursor/hooks",
    ".cursor/agents",
    ".cursor/commands",
]:
    if (R / forbidden).exists():
        errors.append(f"thin-runtime forbidden file present: {forbidden}")

cli_json = R / ".cursor" / "cli.json"
if cli_json.exists():
    try:
        cfg = json.loads(cli_json.read_text(encoding="utf-8"))
        deny = " ".join(cfg.get("permissions", {}).get("deny", []))
        if "**/.env" not in deny:
            warnings.append(".cursor/cli.json should deny read/write of .env and credential files")
        if "git push" not in deny:
            warnings.append(".cursor/cli.json should deny git push / terraform / kubectl mutations")
    except Exception:
        pass

execute_skill = R / ".cursor" / "skills" / "execute-work" / "SKILL.md"
if execute_skill.exists():
    t = execute_skill.read_text(encoding="utf-8")
    if "do not call" not in t.lower() or "scripts/ae-sdlc/*" not in t:
        warnings.append("execute-work should explicitly forbid harness helper-script calls")

print("Thin command-driven AE-SDLC validation")
for w in warnings:
    print("WARNING:", w)
for e in errors:
    print("ERROR:", e)
print(("FAILED" if errors else "OK") + f": {len(errors)} error(s), {len(warnings)} warning(s)")
raise SystemExit(1 if errors else 0)
