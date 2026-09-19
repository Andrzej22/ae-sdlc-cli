#!/usr/bin/env python3
"""Thin sequential background scheduler: one fresh Codex run per READY spec."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from work_store import ROOT, WorkStore

SCHEMA = ROOT / ".ai-sdlc" / "schemas" / "execute-result.schema.json"
RUNTIME = ROOT / ".ai-sdlc-runtime" / "runs"


def codex_exec(spec_id: str, output: Path, model: str | None) -> int:
    prompt = f"""$execute-work {spec_id}\n\nBackground workflow constraints:\n- runner_owns_status=true\n- implement only this spec\n- do not call ./ai-sdlc or scripts/ai-sdlc/*\n- do not spawn subagents or run quality/review/verification phases\n- return the structured result and stop\n"""
    cmd = [
        "codex", "exec", "--ephemeral",
        "--config", 'default_permissions="project-edit"',
        "--output-schema", str(SCHEMA),
        "-o", str(output),
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    return subprocess.run(cmd, cwd=ROOT).returncode


def load_result(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "FAILED", "blockers": [f"invalid structured result: {exc}"], "summary": "Codex result could not be parsed."}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workflow")
    ap.add_argument("scope")
    ap.add_argument("--model")
    ap.add_argument("--max-specs", type=int)
    args = ap.parse_args(argv)

    if shutil.which("codex") is None:
        raise SystemExit("codex CLI not found on PATH")

    wf = ROOT / ".ai-sdlc" / "workflows" / f"{args.workflow}.toml"
    if not wf.exists():
        raise SystemExit(f"workflow not found: {wf}")
    cfg = tomllib.loads(wf.read_text(encoding="utf-8"))
    if int(cfg.get("parallelism", 1)) != 1:
        raise SystemExit("Thin runner supports parallelism=1 only.")

    max_specs = args.max_specs or int(cfg.get("max_specs", 10))
    continue_on_failure = bool(cfg.get("continue_on_failure", True))
    selected_status = str(cfg.get("selection", {}).get("status", "READY"))

    store = WorkStore()  # one scan for the whole run
    RUNTIME.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = RUNTIME / f"{run_id}-{args.workflow}"
    run_dir.mkdir(parents=True, exist_ok=False)

    report = {
        "workflow": args.workflow,
        "scope": args.scope,
        "started_at": run_id,
        "completed": [],
        "blocked": [],
        "failed": [],
        "skipped": [],
    }

    attempted: set[str] = set()
    count = 0

    while count < max_specs:
        eligible = [r for r in store.eligible_specs(args.scope, selected_status) if r.id not in attempted]
        if not eligible:
            break
        rec = eligible[0]
        attempted.add(rec.id)
        count += 1

        store.set_status(rec.id, "IN_PROGRESS")
        rec.meta["status"] = "IN_PROGRESS"  # keep in-memory snapshot synchronized

        output = run_dir / f"{rec.id}.json"
        rc = codex_exec(rec.id, output, args.model)
        result = load_result(output) if output.exists() else {
            "status": "FAILED", "blockers": [f"codex exited {rc}"], "summary": "No structured result produced."
        }
        outcome = result.get("status")

        if rc == 0 and outcome == "COMPLETED":
            store.set_status(rec.id, "DONE")
            rec.meta["status"] = "DONE"
            report["completed"].append({"id": rec.id, "summary": result.get("summary", "")})
        elif outcome in {"BLOCKED", "NEEDS_INPUT"}:
            blocker = "; ".join(result.get("blockers") or []) or result.get("summary") or "needs input"
            store.set_status(rec.id, "BLOCKED", blocker=blocker)
            rec.meta["status"] = "BLOCKED"
            report["blocked"].append({"id": rec.id, "reason": blocker})
            if not continue_on_failure:
                break
        else:
            blocker = "; ".join(result.get("blockers") or []) or result.get("summary") or f"codex exited {rc}"
            store.set_status(rec.id, "BLOCKED", blocker=blocker)
            rec.meta["status"] = "BLOCKED"
            report["failed"].append({"id": rec.id, "reason": blocker})
            if not continue_on_failure:
                break

    # Record ready specs that could not run because dependencies never became DONE.
    for rec in store.specs_for_scope(args.scope, status="READY"):
        if rec.id not in attempted:
            report["skipped"].append({"id": rec.id, "reason": "not dependency-eligible or max_specs reached"})

    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"run_report={report_path.relative_to(ROOT)}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
