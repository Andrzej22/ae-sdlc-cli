# Thin Runtime Architecture

```text
User
 ├─ prepare-work ──> durable epic/spec artifacts
 ├─ execute-work ──> direct implementation
 └─ work-status  ──> deterministic local query

Scheduler
 └─ run-workflow
      └─ one fresh headless agent execution per READY spec
```

The implementation path intentionally avoids orchestration layers after code generation.

## Why

The harness should improve specification, persistence, and unattended scheduling without multiplying token cost through repeated model/tool loops.

Product decomposition and implementation execution are separate concerns. An epic may contain one spec; that is completely valid. A spec is the default executable unit.

## Runtime budget principle

Harness-specific tool calls inside `execute-work` should normally be one `ae-sdlc work start` and one `ae-sdlc work finish` per spec.

The model may use ordinary repository read/search/edit commands needed to implement the feature, but it should not call any other harness script.

## Unattended runs

Run one workflow runner per project. `ae-sdlc workflow` holds a lock under `.ae-sdlc-runtime/` that stops a second runner on the same checkout. Nothing stops a runner on another machine: Jira and Linear status changes are not atomic compare-and-swap operations, so the runner re-reads status and revision before it starts a spec but cannot guarantee exclusive ownership.

| Run outcome | Status | In the run report |
| --- | --- | --- |
| `COMPLETED`, agent exit 0 | `DONE` | `completed` |
| `BLOCKED`, `NEEDS_INPUT` | `BLOCKED`, reason from the agent | `blocked` |
| `FAILED`, `PARTIAL`, agent crash, no result | `BLOCKED`, reason starts with `Execution failed:` | `failed` |

The runner never repeats a spec by itself. A stopped spec, including one left `IN_PROGRESS` by a crash, runs again only after `ae-sdlc work recover <ID> --requeue`.

Before it updates the tracker, the runner saves the update under `.ae-sdlc-runtime/pending/`, and removes it once the provider confirms. If the update fails, the code is already written: `ae-sdlc work recover [ID]` retries only the update and starts no agent.

The runner gives the agent the newest comments, limited by `[comments]` in the workflow file, as untrusted data. The agent returns findings in its result, and the runner posts them only to the spec or items linked to it, using a stable key that prevents duplicates. An epic becomes `DONE` only when it has specs and all of them are `DONE`.

The runner compares a spec's revision before and after its agent runs and lists a difference under `remote_edits` in the report. The outcome still applies, because the code is already written.
