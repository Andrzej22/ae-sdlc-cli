---
name: prepare-work
description: Turn a prompt or existing product item into the minimum durable epic/spec artifacts needed for implementation. Ask blocking questions when needed; do not implement application code. Use only when explicitly invoked as /prepare-work.
disable-model-invocation: true
---

# Prepare Work

## Goal

Convert intent into the smallest useful implementation contract.

The default executable artifact is a **spec**, not a story/task hierarchy.

## Supporting skills

Load by reading the skill's SKILL.md (or invoking it). Use them as knowledge sources, not personas:
no mentoring, quizzing, or mock-interview behavior. Any question they
surface goes through the blocking-question rule (step 3).

- `pragmatic-architect` — always, after a bounded look at the code that the
  feature will touch. Output → spec "Design & Boundaries".
- `ai-engineering-mentor` — only if the work touches LLM calls, agents, RAG,
  embeddings, evals, guardrails, or AI streaming UI. Output → spec "AI Design".

Precedence: this skill's rules (minimal artifacts, no story/task hierarchy,
no application code) override anything the supporting skills suggest.

## Procedure

1. Search `docs/product/` directly for matching IDs/titles before creating anything.
2. Separate confirmed requirements, constraints, assumptions, risks, dependencies, non-goals, and blocking questions.
3. Ask only questions whose answer materially changes behavior, permissions/security, data handling, compatibility, destructive behavior, or scope.
4. In non-interactive mode, never invent a blocking decision. Persist the question and return `NEEDS_INPUT`.
5. Prefer the smallest artifact shape:
   - broad coherent feature -> `epic.md` + one `spec.md` by default;
   - genuinely independent implementation chunks -> epic + multiple specs;
   - already bounded work -> standalone spec.
6. Do **not** mechanically create stories/tasks for backend/frontend/database/tests.
7. For any feature touching an entity, design the client-facing API (see "Client API Design Rule" in AGENTS.md): CRUD completeness, access management, role/permissions in responses, list filtering/pagination, error codes. Record it in the spec's "API Surface" section and list deferred endpoints explicitly.
8. Write observable acceptance criteria and relevant negative/boundary behavior.
9. Mark a spec `READY` only when implementation can proceed without inventing material product behavior.
10. Persist artifacts by editing/creating markdown files directly. Do not call harness helper scripts to do bookkeeping.
11. Stop before application-code implementation.

## ID conventions

Use repository-local sequential IDs:

```text
EPIC-001
SPEC-001
```

Scan existing product artifacts once and choose the next unused ID.

## Typical broad feature

```text
docs/product/epics/EPIC-001-feature-name/
├── epic.md
└── spec.md
```

If multiple specs are truly necessary:

```text
docs/product/epics/EPIC-001-feature-name/
├── epic.md
└── specs/
    ├── SPEC-001-first-slice.md
    └── SPEC-002-second-slice.md
```

## Final response

Report created/updated artifact paths, ready spec IDs, and any blocking questions.

When an output schema is supplied, satisfy it exactly.
