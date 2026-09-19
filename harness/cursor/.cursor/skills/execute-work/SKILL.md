---
name: execute-work
description: Implement a READY spec, multiple specs, or an epic using a thin path close to a normal Cursor agent session. No automatic helper scripts, subagents, quality gates, review loops, or durable planning/verification artifacts. Use only when explicitly invoked as /execute-work.
disable-model-invocation: true
---

# Execute Work

## Goal

Implement ready product work with minimal harness overhead.

## Hard runtime rules

During this skill:

- **do not call** `./ae-sdlc`;
- **do not call** `scripts/ae-sdlc/*`;
- **do not invoke** another Cursor agent session;
- **do not spawn** Task/subagents (including researcher, verifier, reviewer, Bugbot, or security-review agents);
- **do not read** quality-policy/quality-gate files;
- **do not create** `plan.md`, `verification.md`, `review.md`, or security-review artifacts;
- **do not start** a separate post-implementation verification/review phase;
- **do not repeatedly rescan** the repository after you already found the relevant files.

Use direct repository search/read/edit operations only.

## Procedure

### 1. Resolve scope once

Accept:

- a spec ID/path;
- an explicit list of specs;
- an epic ID.

For an epic, locate its `READY` specs under `docs/product/` once.

Do not expand product scope beyond the selected artifacts.

### 2. Check readiness cheaply

Implement only `READY` specs.

If a selected spec is `DRAFT` or `BLOCKED`, report the exact blocker instead of guessing.

### 3. Read minimum context

Read the selected spec(s), then inspect only source/config/test files relevant to the requested implementation.

Prefer one bounded search followed by direct file reads over repeated broad scans.

### 4. Implement as one coherent batch

For several related specs in one interactive invocation, implement them as one coherent coding batch rather than repeating a full lifecycle for each spec.

Follow existing repository patterns. Avoid unrelated refactors and dependency changes.

Tests may be written when the spec explicitly requires them or when they are naturally part of the requested feature implementation.

### 5. Minimal command execution

There is **no generic quality-gate phase**.

After implementation, run at most one targeted command only when:

- the spec explicitly names/requires it; or
- a very cheap syntax/compile/test command is necessary to establish that the just-written code is runnable.

Do not run full test, lint, typecheck, build, security, or integration suites merely because they exist.

Do not enter iterative "verify everything" loops. If the one required targeted command fails, fix the immediate implementation issue with bounded effort; do not turn the task into an open-ended QA session.

### 6. Persist minimal state

Update each completed selected spec directly:

```yaml
status: DONE
```

If implementation cannot proceed because of missing product input or an external blocker:

```yaml
status: BLOCKED
```

and record a concise blocker in the spec.

When an automation runner explicitly says it owns status, do not edit workflow status; return the structured result and let the runner update it.

### 7. Stop

Once the requested implementation is complete and any explicitly-required targeted command has finished, stop. Do not add an independent review phase.

## Final response

Keep it compact:

- work implemented;
- main files changed;
- targeted command run, if any;
- blockers, if any.

When an output schema is supplied, satisfy it exactly.
