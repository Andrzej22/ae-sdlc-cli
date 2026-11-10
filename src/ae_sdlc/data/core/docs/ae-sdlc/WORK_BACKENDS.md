# Work backends

Epics and specs live in one active backend: `local` (Markdown under `docs/product/`, the default), `linear` or `jira`. Code is always written to this repository; only the work items move.

| Canonical concept | local | Linear | Linear with `project` set | Jira |
| --- | --- | --- | --- | --- |
| Where work lives | `docs/product/` | the team | one project of the team | the project |
| Epic | `docs/product/epics/EPIC-*.md` | a new Project | Issue labelled `ae-sdlc-epic` | Epic |
| Spec | Markdown file | Issue | sub-issue of its epic, or a standalone Issue | Task, or the configured work type |
| Dependency | `dependencies:` frontmatter | blocked-by relation | blocked-by relation | `Blocks` link |
| Comments | not supported | yes | yes | yes |

## Selecting a backend

```bash
./ae-sdlc backend connect linear   # or: jira. Stores credentials and discovers settings
./ae-sdlc backend doctor           # verifies the active backend with real read-only calls
./ae-sdlc backend use linear       # refuses a backend that fails its checks
./ae-sdlc backend show
```

Remote backends need the installed `ae-sdlc` package (`pipx install`, `uv tool install`); `local` needs neither the package nor credentials. The selection and non-secret settings are in `.ae-sdlc/work.toml`. With no file, the backend is `local`.

```toml
[work]
backend = "linear"

[linear]
team = "Andrzej22"
project = "ae-sdlc-test"   # optional; see below
```

### Linear: one project for all work

Without `project`, every epic becomes a new Linear Project. With `project` set under `[linear]`, all work goes into that one project, as with a Jira project: an epic is an Issue labelled `ae-sdlc-epic` and its specs are sub-issues. The project can be created by hand in Linear; `backend connect linear` lists the team's projects to choose from, or set the project's name, `P-…` identifier or slug yourself. ae-sdlc never changes the project's own status, and `status`, `work list` and `workflow` take the epic's issue key (`AND-7`), not the project. Changing `project` later does not move existing work.

The plan's suggested `[work.context]` and `[work.execution]` tables are not read; comment limits are `[comments]` in the workflow file. Such tables are kept when `work.toml` is rewritten.

### Local fallback and switching

`./ae-sdlc backend use local` returns to the Markdown workflow at any time. Switching only changes which backend new commands use: nothing is copied, deleted or synchronized, and each backend keeps its own work. A tracker update still owed to the previous backend stays saved; switch back and run `./ae-sdlc work recover` to finish it.

## Credentials and permissions

The first match is used, per provider:

1. Environment: `LINEAR_API_KEY`; `ATLASSIAN_SERVICE_ACCOUNT_KEY`, or `ATLASSIAN_EMAIL` with `ATLASSIAN_API_TOKEN`.
2. `~/.config/ae-sdlc/credentials.toml` (respects `XDG_CONFIG_HOME`; `AE_SDLC_CREDENTIALS_FILE` overrides the path), which must have mode `600`.

`backend connect` writes that file through a hidden prompt. Never put a token in `work.toml` or on a command line.

Coding agents never inherit `LINEAR_*` or `ATLASSIAN_*`. For interactive `prepare-work` and `execute-work`, whose agent runs `./ae-sdlc work` itself, credentials found in the environment are moved to a temporary mode-600 file for the length of the run; `AE_SDLC_CREDENTIALS_FILE` gives the agent its path, not the secret, and the file is deleted afterwards. Credentials from `credentials.toml` are used as they are.

**Linear** needs an API key whose user can read the team and create issues, projects, labels and comments.

**Jira** needs a *scoped* Atlassian API token or service-account key with read and write access to Jira, on a project where the user can create issues and links, transition issues and comment. An organization admin must allow API-token access to the Atlassian MCP server. Otherwise `doctor` reports a permission failure and only OAuth is possible, which this release does not automate.

## Workflow status mapping

| Canonical | Linear (by state type) | Jira (saved in `[jira]` by `backend connect`) |
| --- | --- | --- |
| `DRAFT` | backlog or triage | `status_draft` |
| `READY` | unstarted | `status_ready`; plus the label `ae-sdlc-ready` when it shares a status with `DRAFT` |
| `IN_PROGRESS` | started | `status_in_progress` |
| `BLOCKED` | a native Blocked state, or the label `ae-sdlc-blocked` | `status_blocked`, or the label `ae-sdlc-blocked` |
| `DONE` | completed | `status_done` |

In Linear, `status_<canonical>` in `[linear]` pins a state by name. A canceled Linear issue never runs and never satisfies a dependency. A Jira status outside the mapping is shown as it is, never runs and never satisfies a dependency. Provider workflows are never modified.

## Comments

Before a spec starts, its newest comments are given to the agent as untrusted data; `[comments]` in the workflow file sets `limit` and `max_chars`. In a headless run the agent has no tracker credentials: it returns findings in its result, and the runner posts them only to the spec, its epic or items linked to it, at most 5 per run, with a stable key that prevents duplicates. Dropped findings are listed in the run report. A completed spec receives one summary comment.

## Headless usage

```bash
export LINEAR_API_KEY=...
./ae-sdlc --provider claude workflow nightly ENG-PROJECT --max-specs 5
```

A `workflow` run starts one fresh agent per eligible spec, in dependency order, and writes `.ae-sdlc-runtime/runs/<run>/report.json`. It exits non-zero if a spec failed, the tracker failed, or an update is still owed. `LINEAR_*` and `ATLASSIAN_*` are removed from the agent's environment. A lock allows one runner per checkout.

## Recovery

| Situation | Command | Effect |
| --- | --- | --- |
| The run finished but the tracker update failed (`pending_updates` in the report) | `./ae-sdlc work recover [ID]` | Finishes the status and comments. Starts no agent |
| A spec ended `BLOCKED` and the cause is resolved | `./ae-sdlc work recover ID --requeue` | Back to `READY`, so that the next run picks it up |
| A crash left a spec `IN_PROGRESS` | `./ae-sdlc work recover ID --requeue` | Same. Check the working tree first |
| `prepare-work` failed part-way | Publish the same document again | Existing work is reused, not duplicated |

Nothing repeats a spec automatically. A failed run has a blocker that starts with `Execution failed:`, and a product question has the agent's own reason.

If `remote_edits` in the report lists a spec, someone changed it while its agent ran. Its outcome still applies; compare the current spec with what was implemented.

## Troubleshooting

| Exit code | Meaning | First step |
| --- | --- | --- |
| 2 | invalid input | fix the JSON payload or arguments |
| 3 | not found | check the id and the active backend (`backend show`) |
| 4 | revision conflict | read the item again, then retry |
| 5 | invalid transition | `./ae-sdlc work get ID`; the message names the status or dependency |
| 6 | configuration or missing capability | `./ae-sdlc backend doctor` |
| 7 | authentication or permission | check the token, its scopes and `chmod 600` on the credentials file |
| 8 | rate limit, timeout or outage | retry later; for a finished run use `work recover` |

```bash
./ae-sdlc backend show                 # active backend and settings
./ae-sdlc backend doctor               # credential source, reachability, required tools, mapping
./ae-sdlc status --blocked             # blocked work and reasons
./ae-sdlc work get ID                  # one item as JSON
./ae-sdlc work recover                 # finish owed updates and list what remains
```

## Known limitations

- Credential mode `600` protects against commits and other OS users. It does not stop a coding agent running as the same user with shell access from reading the file, including the temporary file of an interactive run.
- Jira and Linear have no conditional writes. Status and revision are re-read before a spec starts, but runners on different machines can still race; run one per project.
- Edits made during an agent run are reported, not merged. Detection relies on the tracker's update time, so a comment or other activity on the item can also be listed.
- A Jira workflow without a direct transition to a mapped status fails with an error; the workflow is never changed for you.
- Only one backend is active per workflow. There is no migration or synchronization between backends.
- The local backend has no comments.
- Runs are sequential: `parallelism` must be 1.
