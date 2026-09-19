# AI-SDLC (thin command-driven runtime)

Installed by `ae-sdlc init`. One shared set of durable artifacts, one skill set, one or more agent CLIs.

## Skills

- `prepare-work` — turn a prompt or existing item into durable epic/spec artifacts.
- `execute-work` — implement a spec, several specs, or an epic with a minimal runtime path.
- `run-workflow` — run a named unattended workflow over READY specs.
- `work-status` — inspect durable work state.

How each agent invokes them is listed in `AGENTS.md` (`/name` for Claude Code, Cursor, opencode and Antigravity; `$name` for Codex).

## Shell entrypoint

```bash
./ai-sdlc status [--ready|--blocked] [SCOPE]
./ai-sdlc prepare-work "<request>"
./ai-sdlc execute-work SPEC-001
./ai-sdlc workflow nightly EPIC-001
./ai-sdlc validate
```

With several providers installed choose one via `--provider <name>` or `AE_SDLC_PROVIDER=<name>`.
The installed providers are listed in `.ai-sdlc/manifest.json`.

## Layout

```text
docs/product/            epics and executable specs
docs/project-memory/     cross-cutting project knowledge
.ai-sdlc/                artifact-store config, result schemas, workflows, manifest
scripts/ai-sdlc/         work_store, runner, validator, providers/<name>.py adapters
```

If `ae-sdlc init` found a file that already existed with different content, the harness version was
written next to it as `<file>.ae_sdlc`. Merge those by hand, then delete the sidecars.
