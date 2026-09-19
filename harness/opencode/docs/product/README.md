# Product Work

This directory is the durable product source of truth for the thin AE-SDLC.

Prefer epics + executable specs. Do not create stories/tasks unless your repository already uses them for another reason outside this harness.

`$prepare-work` creates/updates these files directly. `$execute-work` reads them directly and should not use bookkeeping helper scripts.