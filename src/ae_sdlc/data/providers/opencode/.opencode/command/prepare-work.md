---
description: Turn a prompt or existing product item into the minimum durable epic/spec artifacts needed for implementation. Ask blocking questions when needed; do not implement application code.
argument-hint: "<request or existing item ID>"
---

# Prepare Work

## Goal

Convert intent into the smallest useful implementation contract.

The default executable artifact is a **spec**, not a story/task hierarchy.

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
7. Write observable acceptance criteria and relevant negative/boundary behavior.
8. Mark a spec `READY` only when implementation can proceed without inventing material product behavior.
9. Persist artifacts by editing/creating markdown files directly. Do not call harness helper scripts to do bookkeeping.
10. Stop before application-code implementation.

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

When an explicit output schema is included in the prompt, satisfy it exactly.

---

$ARGUMENTS