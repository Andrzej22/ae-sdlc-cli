---
name: prepare-work
description: Turn a prompt or existing product item into the minimum durable epic/spec artifacts needed for implementation. Ask blocking questions when needed; do not implement application code.
---

# Prepare Work

## Goal

Convert intent into the smallest useful implementation contract.

The default executable artifact is a **spec**, not a story/task hierarchy.

## Supporting skills

Load by skill name. Use them as knowledge sources, not personas:
no mentoring, quizzing, or mock-interview behavior. Any question they
surface goes through the blocking-question rule (step 3).

- `pragmatic-architect` — always, after a bounded look at the code that the
  feature will touch. Output → spec "Design & Boundaries".
- `ai-engineering-mentor` — only if the work touches LLM calls, agents, RAG,
  embeddings, evals, guardrails, or AI streaming UI. Output → spec "AI Design".

Precedence: this skill's rules (minimal artifacts, no story/task hierarchy,
no application code) override anything the supporting skills suggest.

## Procedure

1. Find existing work once, before creating anything: `./ae-sdlc work lookup "<keywords>"`, and `./ae-sdlc work get <ID>` for an item named in the request.
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
10. Write the content as JSON to `.ae-sdlc-runtime/publish.json` (format below) and run `./ae-sdlc work publish --json-file .ae-sdlc-runtime/publish.json`. It works the same on every backend. Do not write epic/spec files under `docs/product/` yourself.
11. Use the IDs and URLs the command returns; never invent an ID. On a non-zero exit, nothing is confirmed except what `partial` lists: fix the cause and publish the same document again, which does not duplicate work.
12. Stop before application-code implementation.

## Publish format

```json
{
  "epic": {"title": "...", "description": "<Markdown>", "status": "READY"},
  "specs": [
    {"ref": "api", "title": "...", "description": "<Markdown>", "status": "READY"},
    {"ref": "ui", "title": "...", "description": "<Markdown>", "status": "DRAFT", "depends_on": ["api", "ENG-12"]}
  ],
  "blocking_questions": ["..."]
}
```

- `epic` is optional; `{"epic": {"id": "ENG-1"}}` adds the specs to an existing epic.
- `description` is the complete document, following the sections of `docs/product/_templates/spec.md` or `epic.md`, without the frontmatter. Record blocking questions in its "Blocking Questions" section.
- `status` is `DRAFT` or `READY`, default `DRAFT`. While `blocking_questions` is non-empty, the command stores `READY` as `DRAFT`.
- `depends_on` holds the `ref` of an earlier spec in this document, or the ID of existing work.
- To change existing work, give its `id` and only the fields that change. An item returned with `"reused": true` already existed and was left unchanged.
- IDs are assigned by the active backend (`SPEC-001` locally, the provider's key remotely).

## Final response

Report the IDs and URLs returned by the publish command, ready spec IDs, and any blocking questions.

When an output schema is supplied, satisfy it exactly.
