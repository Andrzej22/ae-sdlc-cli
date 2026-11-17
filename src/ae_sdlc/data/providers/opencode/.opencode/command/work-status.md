---
description: Inspect durable epic/spec state without implementation. Prefer the deterministic status command so no LLM reasoning is spent reconstructing workflow state.
argument-hint: "[EPIC-or-SPEC | --ready | --blocked]"
---

# Work Status

Use the Bash tool to run:

```bash
ae-sdlc status <EPIC-or-SPEC>
```

or:

```bash
ae-sdlc status --ready
ae-sdlc status --blocked
```

This is a deterministic query of the active work backend. Do not implement code.

---

$ARGUMENTS