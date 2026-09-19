# Thin Command-Driven Cursor SDLC Harness

A minimal repo-local Cursor harness optimized for **low runtime/token overhead**.

The user-facing interface is intentionally small:

```text
/prepare-work   -> turn an idea/request into durable epic/spec artifacts
/execute-work   -> implement one spec, several specs, or an epic
/run-workflow   -> execute ready specs unattended, one fresh headless Cursor Agent CLI run per spec
/work-status    -> inspect durable state
```

Shell/background equivalents:

```bash
./ae-sdlc prepare-work "Add invoice retry support"
./ae-sdlc execute-work SPEC-001
./ae-sdlc execute-work EPIC-001
./ae-sdlc workflow nightly EPIC-001
./ae-sdlc status EPIC-001
```

## What changed from the heavier harness

The default implementation path deliberately removes:

- automatic Verifier/Reviewer/Researcher subagents;
- quality profiles and automatic quality escalation;
- quality-gate scripts and quality CI;
- post-implementation verification/review loops;
- persistent `plan.md`, `verification.md`, and `review.md` artifacts;
- per-step `artifact_store.py` calls;
- hook scripts that execute on every shell/tool action;
- stories/tasks as mandatory decomposition layers.

The goal is:

```text
prepare-work = reasoning-heavy product/spec work
execute-work = thin implementation path, close to a bare Cursor agent session
run-workflow = deterministic scheduler + fresh headless Cursor Agent CLI run per ready spec
work-status  = deterministic local query, no model
```

---

## 1. Prepare work

Use when the request is vague, broad, or needs product decomposition:

```text
/prepare-work We need organizations to invite users with roles and expiration
```

The skill may ask blocking questions. It creates the **minimum useful artifact set**.

Default broad feature shape:

```text
docs/product/epics/EPIC-001-organization-invitations/
├── epic.md
└── spec.md
```

If the feature genuinely contains independently executable chunks, it may create multiple specs under `specs/`.

It should **not** mechanically create backend/frontend/database/testing stories.

A bounded request may become a standalone spec without an epic.

### Non-interactive prepare

```bash
./ae-sdlc prepare-work "Add organization invitations"
```

If a blocking decision is missing, the run returns `NEEDS_INPUT` and persists the exact question instead of inventing an answer.

---

## 2. Execute work

Use when work is already `READY`:

```text
/execute-work SPEC-001
```

Multiple specs:

```text
/execute-work SPEC-001 SPEC-002
```

Whole epic:

```text
/execute-work EPIC-001
```

The default hot path is intentionally short:

```text
resolve spec(s)
-> read minimum relevant context
-> inspect relevant source files
-> implement the coherent change
-> optionally run at most one explicitly-required targeted command
-> update status
-> stop
```

### What it does NOT do by default

```text
no researcher
no verifier
no reviewer
no security-review phase
no quality-policy classification
no generic quality-gate runner
no full-suite ritual
no plan/review/verification documents
no harness helper-script calls from the model
```

Tests may still be **written as part of the implementation** when the spec requires them. That is different from running a separate verification phase afterward.

For several related specs in an interactive invocation, Cursor treats them as one coherent implementation batch instead of repeating a full lifecycle per spec.

For many independent specs, prefer the background workflow so each spec gets a fresh bounded context.

---

## 3. Run an overnight/background workflow

```bash
./ae-sdlc workflow nightly EPIC-001
```

The scheduler:

1. scans artifact metadata once;
2. resolves ready specs and dependencies in memory;
3. marks one spec `IN_PROGRESS`;
4. starts **one fresh headless `cursor agent -p`** for that spec;
5. reads the structured result;
6. updates status directly in-process;
7. moves to the next eligible spec.

It does not invoke `artifact_store.py`, quality scripts, verifier/reviewer agents, or nested workflows.

The bundled runner is sequential by design. Safe parallel mutation would require worktree/isolation and is intentionally not included in this thin version.

Workflow config:

```text
.ae-sdlc/workflows/nightly.toml
```

The runner writes operational reports to:

```text
.ae-sdlc-runtime/runs/
```

which is git-ignored.

---

## 4. Inspect status

Interactive:

```text
/work-status EPIC-001
```

Shell, without an LLM:

```bash
./ae-sdlc status EPIC-001
./ae-sdlc status --ready
./ae-sdlc status --blocked
```

`status` reads repository metadata directly in one process.

---

## Artifact Store

Durable accepted product work lives in:

```text
docs/product/
```

Cross-cutting reusable project knowledge lives in:

```text
docs/project-memory/
```

Conversation history is not the source of truth.

### Minimal lifecycle

```text
DRAFT -> READY -> DONE
          |       
          +-> BLOCKED
```

`IN_PROGRESS` is mainly useful for unattended runs.

---

## Install

Copy the bundle into the repository root, then run:

```bash
python3 scripts/ae-sdlc/validate_harness.py
```

The validator checks harness structure/config only. It is **not** part of `/execute-work`.

Copy `AGENTS.md`, `.cursor/`, `.ae-sdlc/`, `docs/`, `scripts/`, `ae-sdlc` and `.gitignore` entries into the repository root.
Review `.cursor/cli.json` (CLI deny rules) and `.cursor/rules/security.mdc` for your repository.
Requires the Cursor Agent CLI on `PATH` (`agent`, `cursor-agent`, or `cursor agent`).

---

## Recommended usage

Use `/prepare-work` when requirements need clarification or decomposition.

Use `/execute-work` directly when a spec is already ready.

Use normal Cursor prompts for ad-hoc questions, explanations, tiny edits, or exploration that does not need durable SDLC state.

Use `/run-workflow` only for ready work you are comfortable running unattended.

---

## Cursor specifics

- Skills live in `.cursor/skills/<name>/SKILL.md` and are `disable-model-invocation: true`, so they only run when you type `/prepare-work` etc. Ordinary prompts never trigger the SDLC.
- Project instructions live in `AGENTS.md`. Secrets/mutation guidance also lives in `.cursor/rules/security.mdc` (`alwaysApply: true`).
- Background runs use `cursor agent -p --force --trust --sandbox disabled --output-format json`. `--force` auto-approves tool calls unless they match a deny rule. The runner does not pass `--resume` or `--continue`, so each spec is a fresh session.
- Cursor CLI has no `--json-schema` flag. The runner injects the result schema into the prompt and parses the JSON object out of the `--output-format json` `result` text.
- Cursor CLI has no `--disallowedTools`. Task/subagents are forbidden by skill/project instructions; `WebFetch`/`WebSearch` are denied in `.cursor/cli.json`.
- Secrets deny-rules live in `.cursor/cli.json`. `git push`, `terraform apply|destroy|import` and `kubectl apply|delete|replace|patch` are denied for CLI/headless runs. In the interactive IDE, `.cursor/rules/security.mdc` requires explicit confirmation instead.
- Network: Codex's `project-edit` profile disabled command-level network access. Cursor CLI can deny `WebFetch`/`WebSearch`; shell tools such as `curl` are not blocked.
