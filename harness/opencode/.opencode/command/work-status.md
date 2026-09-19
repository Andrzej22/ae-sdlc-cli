---
description: Inspect durable epic/spec state without implementation. Prefer the deterministic status command so no LLM reasoning is spent reconstructing workflow state.
argument-hint: "[EPIC-or-SPEC | --ready | --blocked]"
---

# Work Status

Use the Bash tool to run:

```bash
./ai-sdlc status <EPIC-or-SPEC>
```

or:

```bash
./ai-sdlc status --ready
./ai-sdlc status --blocked
```

This is a deterministic repository query. Do not implement code.

---

$ARGUMENTS