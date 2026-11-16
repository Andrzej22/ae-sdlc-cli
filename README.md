# ae-sdlc

A small Python CLI that installs a spec-driven workflow for coding agents into an existing repository.

It separates two steps that agents usually blur together:

1. **Prepare**: an agent turns a request into an epic and one or more specs. Each spec has numbered
   requirements, acceptance criteria, dependencies and a status. It writes no application code.
2. **Execute**: an agent (the same one or a different one) implements one `READY` spec, then reports a
   structured result that sets the spec to `DONE` or `BLOCKED`.

Specs are stored outside the agent's session: as Markdown in the repository, or as Linear or Jira issues.
A later session, a different agent, or an unattended nightly run can pick up where the last one stopped.

Supported agent CLIs: **Claude Code, Codex, Cursor, opencode, Antigravity**.

Status: early (`0.1.0`), single maintainer, not published to PyPI.

## What it is not

- It does not provide a model or run agents itself. It calls the agent CLIs you already have installed
  and are logged into.
- It is not an orchestration framework. Each spec runs in one fresh agent process. There is no
  multi-agent loop, review phase or automatic retry.
- It does not merge or open pull requests. The code ends up in your working tree.

## Install

Requires Python 3.11+ and at least one of the agent CLIs on `PATH`.

```bash
git clone <this repo> && cd ae-sdlc
pipx install .                 # or: uv tool install .

cd <your project>
ae-sdlc init --providers claude,codex   # or --providers all; without flags, choose interactively
./ae-sdlc validate
```

`init` never overwrites an existing file. If a file such as `AGENTS.md`, `CLAUDE.md`, `.gitignore` or
`opencode.json` already exists with different content, the harness version is written next to it as
`<file>.ae_sdlc` and listed at the end so you can merge it by hand. Running `init` again is safe.

## Usage

Inside the agent, as skills or slash commands (`/name` in Claude Code, Cursor, opencode and Antigravity;
`$name` in Codex):

| Skill | Does |
| --- | --- |
| `prepare-work` | Turns a request into an epic and specs. Asks blocking questions instead of guessing. |
| `execute-work` | Implements one or more `READY` specs. |
| `work-status` | Shows work items, their status and dependencies. |
| `run-workflow` | Runs a named unattended workflow over `READY` specs. |

The same steps from the shell, headless:

```bash
./ae-sdlc --provider claude prepare-work "Add CSV export to the reports page"
./ae-sdlc --provider codex  execute-work SPEC-001        # a different agent can implement it
./ae-sdlc status --ready
./ae-sdlc --provider codex  workflow nightly EPIC-001 --max-specs 5
```

When several providers are installed, select one per command with `--provider` or `AE_SDLC_PROVIDER`.
`--model` is passed through to the agent CLI.

### Work lifecycle

```text
DRAFT ──(no blocking questions)──> READY ──> IN_PROGRESS ──> DONE
                                                   └───────> BLOCKED ──(work recover --requeue)──> READY
```

- A spec runs only when it is `READY` and all its dependencies are `DONE`.
- `workflow` starts one fresh agent per eligible spec, in dependency order, and writes a report to
  `.ae-sdlc-runtime/runs/<run>/report.json`.
- Nothing is retried automatically. A failed or crashed spec ends `BLOCKED` until you requeue it.
- If the tracker update fails after the code is written, it is saved locally and
  `./ae-sdlc work recover` finishes it without starting an agent.

### Where work items live

```bash
./ae-sdlc backend connect linear    # or: jira. Stores credentials, discovers team/project and statuses
./ae-sdlc backend doctor            # checks the active backend with real read-only calls
./ae-sdlc backend use local         # back to Markdown in docs/product/
```

| | local (default) | Linear | Jira |
| --- | --- | --- | --- |
| Epic | `docs/product/epics/EPIC-*.md` | Project, or a labelled issue in a pinned project | Epic |
| Spec | Markdown with frontmatter | Issue / sub-issue | Task or configured type |
| Dependency | `dependencies:` frontmatter | blocked-by relation | `Blocks` link |
| Comments | no | yes | yes |

Linear and Jira are accessed through their hosted MCP servers. Tracker credentials are never passed
to the coding agent's environment. Details, status mapping, recovery and troubleshooting:
[`WORK_BACKENDS.md`](src/ae_sdlc/data/core/docs/ae-sdlc/WORK_BACKENDS.md), installed as
`docs/ae-sdlc/WORK_BACKENDS.md`.

## What gets installed into your project

```text
ae-sdlc                     shell entrypoint (Python script, no package needed for the local backend)
AGENTS.md / CLAUDE.md ...   agent instructions
.claude/ .codex/ .cursor/ .opencode/ .agents/   per-agent skills and permission config
.ae-sdlc/                   result JSON schemas, workflow definitions, manifest
scripts/ae-sdlc/            work store, runner, validator, one adapter per agent CLI
docs/product/               epic and spec templates (and specs, on the local backend)
docs/project-memory/        constraints, glossary, decisions, lessons shared across sessions
```

The `prepare-work` skill also uses two bundled design skills: `pragmatic-architect` fills the spec's
"Design & Boundaries" section, and `ai-engineering-mentor` fills "AI Design" for LLM-related work.

## Security model

The defaults are deliberately permissive. Restrictions are not built in yet. Tighten them for your
environment before you run agents unattended.

**What an agent can do.** Every adapter starts the agent CLI so that it works without approval prompts.
The agent runs as your user. It can run shell commands and read every file you can read.

| Agent CLI | Execution mode in the adapter | Where to tighten it |
| --- | --- | --- |
| Claude Code | `--permission-mode acceptEdits --allowedTools Bash` (any shell command) | `providers/claude.py`, `.claude/settings.json` |
| Codex | `project-edit` profile: workspace writes, network on | `.codex/config.toml`, `.codex/rules/` |
| Cursor | `--force --trust --sandbox disabled` | `providers/cursor.py`, `.cursor/cli.json` |
| opencode | `--auto`; any bash command except a short denylist | `providers/opencode.py`, `opencode.json` |
| Antigravity | `--dangerously-skip-permissions` | `providers/agy.py` |

Adapters live in `scripts/ae-sdlc/providers/` in your project. Each one marks the permissive part
with a `SECURITY:` comment.

**What the existing measures do and do not do.**

- The tracker credentials file uses mode `600`. This stops other users from reading it. It does not
  stop an agent running as the same user.
- `LINEAR_*` and `ATLASSIAN_*` are removed from the agent's environment, and the prompt tells the
  agent not to call the tracker. This keeps secrets out of the environment. The file on disk is still
  readable.
- Deny rules for `.env` and `credentials.json` match file names only. They are not an isolation boundary.

**Untrusted input.** Spec text and tracker comments become part of the agent's prompt. On Linear or
Jira, anyone who can edit an issue can inject instructions, and the agent may follow them with shell
access (prompt injection, OWASP LLM01 and LLM06). Comments are labeled as untrusted data, but a label
does not enforce anything.

**The unattended gate.** `./ae-sdlc workflow` refuses to start unless one of these holds:

- `AE_SDLC_SANDBOX=1` is set. This is the operator's statement that the process runs in a container
  or VM that sees only this checkout and the network it needs. It is not verified.
- `--allow-unsandboxed` is passed for this run. The run report records `"sandbox": "unsandboxed"`.

Headless `prepare-work` and `execute-work` from the shell are started by you, one at a time, and are
not gated. They use the same permissive adapter flags.

**A reasonable sandbox for nightly runs:** a container running as a non-root user, with only the
checkout mounted (not `$HOME`). Give it a dedicated, narrowly scoped tracker token and allow outbound
traffic only to the model API and the tracker. Use a separate clone or worktree, so a bad run can be
thrown away.

## Technology

- Python 3.11+, standard library for the installed runtime; `hatchling` build
- [`mcp`](https://pypi.org/project/mcp/) Python SDK (streamable HTTP) and `httpx2` for Linear and Jira
- Agent CLIs driven as subprocesses in headless mode. Claude Code, Codex and Antigravity enforce the
  result JSON schema in the CLI. Cursor and opencode get the schema in the prompt. The runner marks a
  spec `DONE` only on exit code 0 with status `COMPLETED`; anything else ends `BLOCKED`
- `pytest`, with a fake MCP server for backend tests; `uv` / `pipx` for install

## Development

```bash
uv run --with pytest pytest          # live Linear/Jira tests are opt-in, see tests/test_e2e_live.py
```

Layout: `src/ae_sdlc/` is the installer and the remote-backend code. `src/ae_sdlc/data/core` holds the
shared runtime, docs and schemas copied into projects. `src/ae_sdlc/data/providers/<name>` holds
per-agent skills, config and adapters. `harness/` contains the older per-provider copies and will be
removed once parity is confirmed. Design notes and the backend plan are in `docs/`.

## Known limitations

- One workflow runner per checkout (enforced by a lock). Linear and Jira have no conditional writes, so
  runners on different machines can race.
- Agents run with permissive execution modes and can read the credentials file (mode `600` does not
  stop the same user). See [Security model](#security-model).
- Jira needs a scoped API token or service-account key. OAuth is not automated.
- Edits made to a spec while its agent runs are reported, not merged.
- The runner lock uses `fcntl`, so it runs on Linux, macOS and WSL only. Native Windows is not supported.
