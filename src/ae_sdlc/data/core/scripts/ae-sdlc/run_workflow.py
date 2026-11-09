#!/usr/bin/env python3
"""Thin sequential background scheduler: one fresh headless agent run per READY spec."""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from work_store import ROOT
from work.backend import WorkBackend, WorkBackendError
from work.config import load_backend
from work.models import Status
from work.service import WorkService
import providers

SCHEMA = ROOT / ".ae-sdlc" / "schemas" / "execute-result.schema.json"
RUNTIME = ROOT / ".ae-sdlc-runtime" / "runs"


def agent_exec(adapter, spec_id: str, output: Path, model: str | None) -> int:
    rc, result = adapter.run("execute-work", spec_id, providers.BACKGROUND_TAIL, SCHEMA, model, background=True)
    if result is not None:
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return rc


def load_result(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "FAILED", "blockers": [f"invalid structured result: {exc}"], "summary": "Agent result could not be parsed."}


def main(argv: list[str] | None = None, backend: WorkBackend | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workflow")
    ap.add_argument("scope")
    ap.add_argument("--model")
    ap.add_argument("--max-specs", type=int)
    ap.add_argument("--provider")
    args = ap.parse_args(argv)

    provider = providers.resolve(args.provider)
    adapter = providers.load(provider)
    adapter.check_cli()

    wf = ROOT / ".ae-sdlc" / "workflows" / f"{args.workflow}.toml"
    if not wf.exists():
        raise SystemExit(f"workflow not found: {wf}")
    cfg = tomllib.loads(wf.read_text(encoding="utf-8"))
    if int(cfg.get("parallelism", 1)) != 1:
        raise SystemExit("Thin runner supports parallelism=1 only.")

    max_specs = args.max_specs or int(cfg.get("max_specs", 10))
    continue_on_failure = bool(cfg.get("continue_on_failure", True))
    selected_status = str(cfg.get("selection", {}).get("status", "READY"))

    try:
        service = WorkService(backend or load_backend(ROOT))  # one scan for the whole run
    except WorkBackendError as exc:
        raise SystemExit(str(exc))
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
        eligible = [s for s in service.eligible_specs(args.scope, selected_status) if s.id not in attempted]
        if not eligible:
            break
        spec = eligible[0]
        attempted.add(spec.id)
        count += 1

        service.start_execution(spec.id)

        output = run_dir / f"{spec.id}.json"
        rc = agent_exec(adapter, spec.id, output, args.model)
        result = load_result(output) if output.exists() else {
            "status": "FAILED", "blockers": [f"{provider} exited {rc}"], "summary": "No structured result produced."
        }

        finished = service.finish_execution(spec.id, rc, result, fallback_reason=f"{provider} exited {rc}")
        if finished.disposition == "completed":
            report["completed"].append({"id": spec.id, "summary": finished.detail})
        else:
            report[finished.disposition].append({"id": spec.id, "reason": finished.detail})
            if not continue_on_failure:
                break

    # Record ready specs that could not run because dependencies never became DONE.
    for spec in service.backend.list_specs(args.scope, status=Status.READY):
        if spec.id not in attempted:
            report["skipped"].append({"id": spec.id, "reason": "not dependency-eligible or max_specs reached"})

    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"run_report={report_path.relative_to(ROOT)}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
