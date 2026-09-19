# Thin Runtime Architecture

```text
User
 ├─ /prepare-work ──> durable epic/spec artifacts
 ├─ /execute-work ──> direct implementation
 └─ /work-status  ──> deterministic local query

Scheduler
 └─ /run-workflow
      └─ one fresh headless Cursor Agent CLI run per READY spec
```

The implementation path intentionally avoids orchestration layers after code generation.

## Why

The harness should improve specification, persistence, and unattended scheduling without multiplying token cost through repeated model/tool loops.

Product decomposition and implementation execution are separate concerns. An epic may contain one spec; that is completely valid. A spec is the default executable unit.

## Runtime budget principle

Harness-specific tool calls inside `/execute-work` should normally be zero.

The model may use ordinary repository read/search/edit commands needed to implement the feature, but it should not call harness bookkeeping scripts.
