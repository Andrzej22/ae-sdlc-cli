---
name: run-workflow
description: Run a named unattended workflow over READY specs. Use the deterministic shell runner instead of manually reproducing scheduling logic in the model session.
disable-model-invocation: true
argument-hint: "<workflow-name> <EPIC-or-SPEC>"
allowed-tools: Bash(./ae-sdlc workflow:*)
---

# Run Workflow

For unattended/batch execution, invoke the repository runner once:

```bash
./ae-sdlc workflow <workflow-name> <scope>
```

Example:

```bash
./ae-sdlc workflow nightly EPIC-001
```

The runner owns scheduling and status transitions. It loads artifact metadata in-process and starts one fresh headless Claude Code execution per eligible spec.

Do not manually loop over specs in the current model session when the user explicitly requested the background workflow.

The bundled workflow intentionally has no automatic verification/review/quality phase.
