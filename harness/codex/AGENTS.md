# Codex Project Instructions

## Operating Model

This repository uses a thin command-driven AI-SDLC.

The explicit user-facing skills are:

- `$prepare-work` — turn a prompt or existing item into durable implementation-ready epic/spec artifacts.
- `$execute-work` — implement a spec, multiple specs, or an epic with a minimal runtime path.
- `$run-workflow` — run a named unattended workflow over ready specs.
- `$work-status` — inspect durable work state without changing implementation.

Do not automatically run the SDLC for ordinary prompts. Use these workflows when explicitly invoked or when the user clearly requests the equivalent operation.

## Core Principle

Keep the implementation hot path close to bare Codex.

`$execute-work` must not automatically invoke helper scripts, subagents, quality gates, independent verification, review, security review, or durable planning artifacts.

The harness exists primarily to:

1. turn ambiguous intent into durable specs;
2. persist work state across sessions;
3. implement ready specs with minimal orchestration;
4. schedule multiple ready specs unattended.

## Durable Memory

Conversation/session context is temporary. Durable accepted work belongs in:

```text
docs/product/              epics and executable specs
docs/project-memory/       reusable cross-cutting project knowledge
```

Before creating an artifact, search for an existing ID/title and update the existing source of truth when appropriate.

## Work States

Use only:

```text
DRAFT
READY
IN_PROGRESS
DONE
BLOCKED
```

A spec is the default executable unit. Do not create stories/tasks merely to make the process look complete.

## Implementation Rules

- Do not implement `DRAFT` or `BLOCKED` work by inventing product behavior.
- Inspect only the relevant code before editing.
- Prefer existing repository patterns over new abstractions.
- Keep unrelated refactors out.
- Do not silently weaken requirements or tests.
- Write tests when the spec explicitly requires them or they are naturally part of the requested implementation.
- Do not enter a separate post-implementation verification/review phase unless the user explicitly requests one outside the default harness.
- Do not run full test/lint/build suites unless the user explicitly requests them or the spec explicitly requires them.
- Stop at unresolved material product or destructive/production decisions.

## Script Discipline

During `$execute-work`, do not call `./ai-sdlc`, `scripts/ai-sdlc/*`, or other harness bookkeeping scripts.

Resolve artifacts with direct repository search/read operations, edit the implementation, update the spec status directly, and stop.

## Completion

For normal interactive execution, a spec may be marked `DONE` after the requested implementation is complete. If one targeted command was explicitly required by the spec and it failed, do not mark `DONE`.
