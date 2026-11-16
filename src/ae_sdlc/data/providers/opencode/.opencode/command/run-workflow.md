---
description: Run a named unattended workflow over READY specs. Use the deterministic shell runner instead of manually reproducing scheduling logic in the model session.
argument-hint: "<workflow-name> <EPIC-or-SPEC>"
---

# Run Workflow

For unattended/batch execution, invoke the repository runner once via the Bash tool:

```bash
./ae-sdlc workflow <workflow-name> <scope>
```

Example:

```bash
./ae-sdlc workflow nightly EPIC-001
```

The runner owns scheduling and status transitions. It loads artifact metadata in-process and starts one fresh headless opencode run per eligible spec.

Do not manually loop over specs in the current model session when the user explicitly requested the background workflow.

The bundled workflow intentionally has no automatic verification/review/quality phase.

The runner refuses to start unless `AE_SDLC_SANDBOX=1` is set (the operator declares a sandbox) or the user passes `--allow-unsandboxed`. If it refuses, report that to the user. Never add `--allow-unsandboxed` yourself.

---

$ARGUMENTS