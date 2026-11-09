# ae-sdlc

Installs a thin, command-driven AE-SDLC harness into a project for one or more coding-agent CLIs
(Claude Code, Codex, Cursor, opencode, Antigravity).

```bash
pipx install .            # or: uv tool install .
ae-sdlc init --providers claude,codex      # or: --providers all, or run without flags to pick interactively
cd <project> && ./ae-sdlc validate
```

`init` never modifies an existing file. If a file already exists with different content
(`AGENTS.md`, `CLAUDE.md`, `.gitignore`, `opencode.json`, ...), the harness version is written beside it as
`<file>.ae_sdlc` and listed at the end so you can merge it by hand. Re-running `init` is safe.

Work items live in local Markdown by default, or in Linear or Jira: see `src/ae_sdlc/data/core/docs/ae-sdlc/WORK_BACKENDS.md`
(installed as `docs/ae-sdlc/WORK_BACKENDS.md`).

Run the tests with `uv run --with pytest pytest`. Live tracker tests are opt-in; see `tests/test_e2e_live.py`.

Layout: `src/ae_sdlc/data/core` (shared runtime, docs, schemas), `data/providers/<name>` (per-agent skills/config/adapter).
The old per-provider copies remain in `harness/` until parity is confirmed.
