---
name: work-status
description: Inspect durable epic/spec state without implementation. Prefer the deterministic status command so no LLM reasoning is spent reconstructing workflow state. Use only when explicitly invoked as /work-status.
disable-model-invocation: true
---

# Work Status

Use:

```bash
./ai-sdlc status <EPIC-or-SPEC>
```

or:

```bash
./ai-sdlc status --ready
./ai-sdlc status --blocked
```

This is a deterministic repository query. Do not implement code.
