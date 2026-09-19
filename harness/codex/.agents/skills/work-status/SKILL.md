---
name: work-status
description: Inspect durable epic/spec state without implementation. Prefer the deterministic status command so no LLM reasoning is spent reconstructing workflow state.
---

# Work Status

Use:

```bash
./ae-sdlc status <EPIC-or-SPEC>
```

or:

```bash
./ae-sdlc status --ready
./ae-sdlc status --blocked
```

This is a deterministic repository query. Do not implement code.
