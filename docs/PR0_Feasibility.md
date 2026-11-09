# PR 0 — Baseline, compatibility, and MCP feasibility

Baseline: `main` @ `7e293c7`. Date of probing: 2026-11-09.
Scope: findings, baseline tests, proposed boundaries, blockers. No backend code, no user-facing change, installer untouched.

## 0. Verdict

| Question | Status |
|---|---|
| Local lifecycle documented and testable | **Done** — 48 characterization tests (`tests/`), all passing |
| MCP endpoints reachable, auth model understood | **Done** (unauthenticated probing + official docs) |
| Python MCP client can be driven without an LLM | **Done** — `tools/mcp_probe.py` completed initialize + `tools/list` against both real endpoints with real credentials |
| Interactive OAuth (plan task 4) | **Deferred by decision D5** — discovery metadata only; no browser flow was run |
| Real tool catalog, Linear | **Done** — 60 tools; full read/write round trip in a test project |
| Real tool catalog, Jira | **Done** — scoped token, API-token access enabled by the org: 26 primary tools, 250 in the flat list (43 Jira); full read/write round trip in a test project |
| Python dependency/runtime boundary decided | **Done** — §3 |

**Both providers passed the full required capability round trip. Remote integration is feasible. Interactive OAuth is deliberately deferred (D5).** PR 1 is not blocked.

## 1. Existing architecture and invariants (what PR 1+ must preserve)

Two Python worlds:

1. **Installed package** `ae_sdlc` (pipx/uv tool): `cli.py` (`init` only) + `installer.py`. Stdlib-only, `dependencies = []`.
2. **Generated project runtime** copied into the user's repo: `./ae-sdlc` (shebang `python3`), `scripts/ae-sdlc/{work_store,run_workflow,validate_harness}.py`, `providers/<name>.py`. Runs under the **project's** interpreter, which cannot see the pipx/uv tool environment.

Invariants pinned by tests:

- Installer: never modifies an existing file; different content → `<file>.ae_sdlc` sidecar; re-run is idempotent; only `.ae-sdlc/manifest.json` (if ours) is updated in place, merging providers; `--dry-run` writes nothing. (`test_installer_and_runtime_boundary.py`)
- The generated runtime is **stdlib-only** (AST-checked) and has **no reference to `ae_sdlc`** — there is currently no way for `./ae-sdlc` to reach the installed package.
- All five providers install and pass `./ae-sdlc validate`, alone and together. Thin-runtime forbidden files fail validation.
- Local store: IDs from frontmatter `work_item_id`; Epic ≠ executable (only `spec` runs); dependencies all `DONE` (unknown dependency ⇒ ineligible); statuses `DRAFT/READY/IN_PROGRESS/DONE/BLOCKED`; blocker field set on BLOCKED and cleared otherwise; body and unknown frontmatter keys preserved on rewrite; fixed key order.
- Runner: sequential, one fresh agent per spec, runner owns status (`runner_owns_status=true`), report in `.ae-sdlc-runtime/runs/<ts>-<wf>/report.json`, `parallelism != 1` rejected, each spec attempted once per run, `max_specs`, `continue_on_failure`.
- Agent-facing text forbids `./ae-sdlc` and `scripts/ae-sdlc/*` during `execute-work` (skills + `EXECUTE_TAIL`/`BACKGROUND_TAIL`); `validate_harness` warns if that disappears.

### Quirks found (pinned as `test_QUIRK_*`; each is a decision for the PR that touches it)

| # | Current behavior | Consequence for plan |
|---|---|---|
| Q1 | `set_status` accepts any→any transition (e.g. DONE→DRAFT) | PR 1 acceptance says "reject invalid status transitions" — that is **new** behavior for the local backend, not preserved behavior. Decide the transition table explicitly; keep local permissive or add a table shared by all backends |
| Q2 | Runner maps `FAILED`/`PARTIAL`/non-zero exit/missing result → **BLOCKED + blocker text** | Directly contradicts PR 6 ("infra failure ≠ product blocker"). PR 6 changes local semantics too, or the contract must be backend-specific. Needs a decision |
| Q3 | `COMPLETED` with non-zero exit code ⇒ not DONE (becomes BLOCKED) | Keep; it is the existing "completion semantics" |
| Q4 | Spec left `IN_PROGRESS` by a crash is ignored forever; no recovery | PR 6 recovery is greenfield |
| Q5 | Epic status is never derived or updated | PR 6 "Epic completion" is new behavior |
| Q6 | Run directory named by 1-second timestamp, `mkdir(exist_ok=False)`: two runs in the same second crash | Latent bug; fix when the runner is refactored (PR 6) |
| Q7 | Hand-rolled frontmatter parser drops `#` comments and multi-line values on rewrite | Reuse, don't re-implement, in the local backend; no semantic change allowed |
| Q8 | In-memory snapshot is scanned once per run; runner manually syncs `rec.meta["status"]` | Redundant for indexed records, needed for path-resolved ones. Hidden coupling a `WorkBackend` must absorb |
| Q9 | Interactive `execute-work`/`prepare-work` edit Markdown directly with no script; the **model** owns bookkeeping | The entire bookkeeping model flips for remote backends (PR 5). Largest behavioral change in the project |

Also noted: legacy `harness/` per-provider copies still exist at the repo root (README says kept "until parity is confirmed"); untouched. `status` only supports the local store.

## 2. MCP feasibility

### 2.1 Verified in this session (no credentials)

| Check | Linear | Atlassian |
|---|---|---|
| Endpoint | `https://mcp.linear.app/mcp` (read-only variant `/mcp/readonly`; SSE `/sse` deprecated) | `https://mcp.atlassian.com/v2/mcp` (`?tools=all` = flat paginated list for gateways) |
| Unauthenticated POST | 401, `WWW-Authenticate: Bearer … resource_metadata=…`, scope `read write` | 401 `{"error":"invalid_token"}`, resource metadata advertised |
| OAuth discovery | protected-resource + authorization-server metadata served; PKCE S256; **DCR**, public clients (`none`), `client_id_metadata_document_supported`, `refresh_token`, `jwt-bearer` grant | AS = `auth.atlassian.com/<tenant>`; DCR at `mcp.atlassian.com/v1/register`; PKCE; `refresh_token` |
| Scopes | `read`, `write` | per-product: `read/write/search/delete/manage:jira:agent-interface`, `offline_access`, … |
| Static credential header | `Authorization: Bearer <api key or OAuth token>` | Personal API token: `Authorization: Basic base64(email:token)`. Service-account key: `Authorization: Bearer <key>`. (Docs; the OAuth metadata's `bearer_methods_supported` covers OAuth tokens only) |
| SDK probe, bogus token | HTTP 401 → probe exit 3 | HTTP 401 → probe exit 3 |

### 2.2 Documented but NOT verified under an authenticated identity

**Linear** (docs): OAuth 2.1 + DCR interactively; OAuth token **or Linear API key** as `Authorization: Bearer` for headless; read-only via `/mcp/readonly`, `read` scope, or read-only API key. **Tool names are not published.** Docs only say tools exist for "issues, projects, and comments".

**Atlassian** (docs, "Supported tools" page):
- Primary (visible in `tools/list`): `getJiraIssue`, `createJiraIssue`, `editJiraIssue`, `transitionJiraIssue`, `addOrEditJiraIssueComment`, `searchJiraIssuesUsingJql`, plus `discover`, `executeRead`, `executeWrite`, `executeDestructive`.
- Deferred (reached only via `discover` → `executeRead/Write`): `listJiraIssueTransitions`, `listJiraIssueComments`, `listJiraIssueLinkTypes`, `createJiraIssueLink`, `getJiraIssueTypeMetaWithFields`, `listJiraProjectIssueTypesMetadata`, `listJiraStatuses`, `listJiraIssueChangelogs`, `getJiraCurrentUser`, `getContentFormatGuide`, … (`delete_*` and `manage_*` groups are disabled by default).
- API-token auth must be enabled by an org admin; otherwise only OAuth 2.1 works. Personal tokens and service-account keys are both supported, tokens are **not bound to a cloudId** (the client must pass `cloudId` explicitly), and some tools are unavailable to tokens whose scopes cannot be selected. Classic-scope tokens may expose only Teamwork Graph tools, so a short `tools/list` must not be read as "Jira unsupported". Rate limits are **not stated** in the pages fetched. v1 URLs auto-move to v2 tools on 2027-03-01.

### 2.3 Capability matrix (observed 2026-11-09)

Every row was executed with real credentials in dedicated test projects: Linear `ae-sdlc-test` (`P-AND-1`, issues `AND-5`, `AND-6`) and Jira `AESDLCTEST` (`-1` epic, `-2`, `-3`).

| Required capability | Linear | Jira |
|---|---|---|
| Read issue + full description | `get_issue` | `getJiraIssue` (`full`, `evidence`, `compact` all return it; `compact` omits `parent`) |
| Search | `list_issues(query)` | `searchJiraIssuesUsingJql` |
| Create spec item | `save_issue` (no `id`; `team`, `title`) | `createJiraIssue` |
| Update description | `save_issue(id, description)` | `editJiraIssue(fields.description)` |
| Read status / options | `get_issue.status`; `list_issue_statuses(team)` | `listJiraIssueTransitions` via `executeRead` |
| Transition | `save_issue(id, state)` | `transitionJiraIssue(transitionId)`, returns landed status |
| Read comments | `list_comments(issueId)` | `listJiraIssueComments` |
| Create comments | `save_comment(issueId, body)` | `addOrEditJiraIssueComment` |
| Parent | `save_issue(project)` | `createJiraIssue(parent)`, read via `full`/`evidence` |
| Read dependencies | `get_issue(includeRelations)`, both directions | `issuelinks` in `getJiraIssue(view="evidence")` |
| Create dependencies | `save_issue(blockedBy)` | `createJiraIssueLink` via `executeWrite`; `inwardIssue` blocks `outwardIssue` |
| List items in Epic/Project | `list_issues(project)` | JQL `parent = KEY` |
| Concurrency metadata | `updatedAt` changes on edit | `updated` changes on edit |
| Project / Epic create | `save_project` | `createJiraIssue(issueType="Epic")` |
| Jira project create (test setup only, not a product requirement) | n/a | Unavailable: `createJiraProject` is absent (`manage_jira` group, disabled by default) |
| Markdown fidelity | Headings, lists, table, code block, quote, Unicode preserved | Same content preserved; Markdown is normalized (`-` to `*`, table separator row) |
| Invalid status | `is_error=true`, plain text `Could not find state "..."`; state unchanged | **`is_error=false`**, payload `error: TRANSITION_FAILED`, `status: 400`, `allowedValues` |

Live environment facts:

- **Linear:** one team; statuses `Backlog`, `Todo`, `In Progress`, `In Review`, `Done`, `Canceled`, `Duplicate`. There is no distinct blocked state, so `BLOCKED` needs the plan's label strategy. No projects exist yet.
- **Jira:** one site, `andrzej22.atlassian.net`, Jira `read-write`. Workflows differ per project even on one site: `SCRUM` offers `To Do`, `In Progress`, `In Review`, `Done`, while `AESDLCTEST` offers `To Do`, `In Progress`, `Done`, `Blocked`. `DRAFT` and `READY` share `To Do` in both, so they need the configured strategy; `BLOCKED` is native only where the project defines it. Status mapping must be discovered per project.

Findings that change later PRs:

1. **No conditional writes on either provider's schema.** Neither `save_issue` nor `editJiraIssue` accepts an expected revision or idempotency key. `expected_revision` can only be read-then-compare, with a remaining race. Duplicate prevention needs the recovery lookup from PR 3.
2. **PR 4 does not need its own ADF converter.** Jira tools accepted and returned Markdown; a spec with table, code block, quote, and Unicode kept its content. Output is normalized, so never compare descriptions byte-for-byte or use a content hash as a revision token.
3. **`tools/list` success does not prove access.** The Jira token passed initialize and `tools/list`, then failed each call. Errors arrive as tool results (`is_error`, JSON `statusCode: 401`), not HTTP errors. PR 2 must classify these too, and `backend doctor` must make a real call.
4. **`discover` is natural-language search**, so it is unsuitable for runtime resolution. Pin operation names in the adapter and have `doctor` verify them.
5. **Jira requires `cloudId`** on operations. It belongs in `work.toml`.
6. `listJiraIssueComments` is directly visible in `tools/list`, unlike the docs' grouping. The `?tools=all` list depends on token scopes: 1 tool with the unscoped token, 250 with the scoped one. `deleteJiraIssue`, `createJiraProject`, and `getJiraWorkflows` are absent from it, consistent with those groups being disabled by default.
7. **Three distinct Jira auth failures exist and need separate messages:** bad credential (HTTP 401), token without scopes (tool result 401 "missing the scope claim"), and API-token access not enabled by the org (tool result "You don't have permission to connect via API token").
8. **`discover` returns JSON followed by a plain-text trailer**, so a strict `json.loads` fails. Tool responses must not be assumed to be a single JSON document.
9. **A failed Jira transition reports `is_error=false`.** The failure exists only in the payload (`error`, `status: 400`). Adapters must inspect the payload and confirm `statusName`; trusting `is_error` would mark a failed transition as successful. Linear errors are plain text, not JSON.
10. **JQL `fields=["parent"]` returned no `parent`** in the default view, although the `parent = KEY` filter works. Read the parent through `getJiraIssue`.
11. Comment bodies round-tripped an HTML-comment dedupe marker (`<!-- op:... -->`) on both providers. How each UI displays it was not checked.

### 2.4 Decision for missing capabilities

Per plan rule 13 — no guessed tools or invented semantics. Policy to implement in PR 2's `capabilities.py`:

1. Each backend declares a **required** set of logical operations. Logical op → provider tool name is resolved from the *discovered* catalog (for Jira: `tools/list` plus `discover`), never hard-coded as assumed-present.
2. `backend doctor` reports `OK | MISSING | DENIED` per op. Any **required** op missing ⇒ the backend cannot be activated (`backend use` refuses); no silent degrade.
3. **Optional** ops (dependency links, revision token) degrade only to an *explicit, configured* strategy recorded in `work.toml` and shown by `doctor`, e.g. dependencies as a Markdown "Dependencies" section ⇒ **advisory only, not enforced**, and `doctor` says so. Not enabled by default.
4. If Linear lacks Project creation, Epic→Project is unsupported until a user chooses an explicit fallback (e.g. attach to an existing Project, or parent issue). Native REST/GraphQL fallback stays out per constraint 5 unless this gap is *documented here* and approved.

### 2.5 Headless / interactive auth findings

- Headless: static credentials (Linear API key; Atlassian Basic email+token or service-account Bearer, if org-enabled) travel as a plain header on the HTTP client. Header construction is checked against a local server; acceptance by the real endpoints is unproven until B1. OAuth in unattended runs needs refresh-token storage; defer to "when practical" as the plan says.
- **SDK 2.x flattens HTTP failures**: a 401 surfaces as `MCPError(code=-32603, "Server returned an error response")` inside an `ExceptionGroup`. Auth vs transport vs rate-limit **cannot** be classified from the exception. PR 2 must capture HTTP status itself (httpx2 response hook, as in the probe) or preflight. 
- `mcp==2.3.0` API differs from 1.x: `streamable_http_client(url, http_client=httpx2.AsyncClient)` (headers live on the HTTP client); `streamablehttp_client` is gone. Pin the major.
- The coding-agent processes are not the MCP client in this design: runner-owned status (`runner_owns_status`) and deterministic code mean headless agents need **no** MCP access, so provider network restrictions (`codex` `project-edit` has `network.enabled=false`; Cursor denies WebFetch) do not block remote backends **provided the `./ae-sdlc` process itself runs outside the agent sandbox**. For interactive `execute-work`/`prepare-work` the agent must run `./ae-sdlc work …` via Bash; whether each provider's sandbox permits the resulting outbound network call is the "restrictions relaxed separately" item (constraint 12) and **is not testable here**. Flag to the PR 5 owner.
- Secrets: see the decision in §3.1. The runner launches agents with the full parent env today, so PR 2/6 must scrub `LINEAR_*`/`ATLASSIAN_*` from agent env.

## 3. Decision: dependency / runtime boundary

**MCP and all remote logic live in the installed `ae_sdlc` package; the generated `./ae-sdlc` stays stdlib-only and delegates.**

Why: `mcp==2.3.0` pulls `pydantic>=2.12`, `starlette`, `uvicorn`, `pyjwt[crypto]`, `jsonschema`, `opentelemetry-api`, `httpx2`, … (verified from PyPI metadata) — it cannot be vendored into generated scripts and is not importable from the project interpreter.

Mechanics (to implement in PR 2/PR 5, not now):
- `pyproject`: `mcp>=2.3,<3` becomes a required dependency of the `ae_sdlc` package in PR 2 (decision D3). The generated `./ae-sdlc` stays stdlib-only, so the local path still needs no `mcp` import.
- `./ae-sdlc` resolves the package CLI via, in order: `$AE_SDLC_BIN`, `ae-sdlc` on `PATH` (pipx/uv tool shim). It invokes a **hidden** subcommand set (`ae-sdlc _work …`) with JSON on stdout. No machine-specific absolute paths in tracked files.
- Local backend must keep working with **zero** installed package: `./ae-sdlc status/validate/workflow` on `local` must not require `ae_sdlc` (PR 1 must keep the stdlib path; remote delegation only when `work.toml` selects a remote backend).
- **Version skew** (project scripts vs installed package): add a handshake (manifest `version` vs `ae_sdlc.__version__`) with a clear warning. This respects "do not change upgrade policy": the sidecar mechanism is untouched; the handshake only reports.
- Single source for local parsing: PR 1 should make `work_store.py` the one implementation and import it from the package (e.g. package vendors the same file via `importlib`/shared data), avoiding a second copy.

Tests guard this boundary today (`test_generated_runtime_is_stdlib_only`, `…no_link_back…`). PR 2 will intentionally change the second one.

### 3.1 Decision: credential storage (v1)

Accepted 2026-11-09. This is the v1 meaning of the plan's "environment variables or a secure credential store". Implementation belongs to PR 2; nothing is implemented yet.

**Resolution order** (first match wins, per provider):

1. Environment variables, for CI and headless runs: `LINEAR_API_KEY`; `ATLASSIAN_SERVICE_ACCOUNT_KEY`, or `ATLASSIAN_EMAIL` + `ATLASSIAN_API_TOKEN`.
2. User-level file outside any repository: `$XDG_CONFIG_HOME/ae-sdlc/credentials.toml`, defaulting to `~/.config/ae-sdlc/credentials.toml`, mode `600`. `$AE_SDLC_CREDENTIALS_FILE` overrides the path, for development against test workspaces.

```toml
[linear]
api_key = "lin_api_..."

[atlassian]
email = "you@company.com"
api_token = "..."
# or, instead of email + api_token:
# service_account_key = "..."
```

**Rules:**

- Only non-secret settings (`team`, `project_key`, `cloud_id`) go in `.ae-sdlc/work.toml`.
- The CLI refuses to read the file if group or others have any access, and reports the fix (`chmod 600 <path>`).
- `./ae-sdlc backend connect <provider>` reads secrets through a hidden prompt and writes the file atomically with mode `600`. Secrets are never accepted as command-line arguments.
- `backend doctor` reports only the credential source (`env` or `file`), never the value or a fragment of it. Secrets never appear in stdout, logs, issue text, or agent context.
- Only the installed `ae_sdlc` package reads credentials, and only when a remote backend is active. The local backend needs none.
- The runner removes `LINEAR_*` and `ATLASSIAN_*` from agent subprocess environments. In headless runs the agent returns comments in its structured result and the runner publishes them.
- Atlassian header format: personal token `Basic base64(email:token)`; service-account key `Bearer <key>`. API-token access requires org-admin enablement; without it only OAuth is available.

**Rejected for v1:**

| Option | Reason |
|---|---|
| Project `.env` | The generated `.gitignore` does not ignore it, and the installer does not change an existing `.gitignore`, so it is easy to commit |
| `export` in shell profile as the recommended setup | Agents inherit it, and one `env` call puts the token in model context |
| OS keychain | Unavailable or unreliable on WSL, CI, and servers (`secret-tool` is absent on the development WSL host). May be added later as an additional source |

**Known limitation (must appear in user documentation):** mode `600` protects against accidental commits and other OS users. It does not protect against a coding agent running as the same OS user with shell access, which can deliberately read the file. A separate credential-broker process would be needed for that and is out of scope for v1.

**OAuth:** where org policy rules out API tokens, interactive OAuth is the fallback. Its token storage (the SDK exposes a `TokenStorage` interface) is designed in PR 2 and must follow the same location and permission rules.

## 4. Proposed interface boundaries (for PR 1; nothing implemented)

```
ae_sdlc/work/models.py      WorkItem, WorkComment, Status(enum), RunOutcome(enum)   # pure dataclasses
ae_sdlc/work/backend.py     WorkBackend Protocol + typed errors (NotFound, RevisionConflict,
                            InvalidTransition, CapabilityMissing, AuthError, TransientError)
ae_sdlc/work/service.py     WorkService(backend, config): prepare_execution_context / start_execution /
                            finish_execution; eligibility + dependency rules; the ONLY place with status policy
ae_sdlc/work/backends/local.py   adapts work_store.py (no duplicate parsing)
(later) work/mcp/*, backends/linear.py, backends/jira.py
```

Rules:
- Status policy (transition table, eligibility, dependency check, Q1/Q2 decisions) lives in `service`, not in backends; backends do I/O and normalization only (provider names never leak above `backends/`).
- `WorkBackend` stays small: `get`, `list_specs`, `create`, `update`, `get_dependencies`, `get_comments`, `add_comment`, `transition`. Defer `search`, `get_related`, `get_status_mapping` until a caller exists (plan rule 5). Local implements comments as a no-op/unsupported with explicit capability flags rather than inventing storage.
- Inject the backend into the runner instead of `WorkStore()` instantiation (the only call site is `run_workflow.py:main`, plus `./ae-sdlc status`).
- Config: `.ae-sdlc/work.toml`, default `local` when absent; it is the sole authoritative backend setting (manifest.json carries none).

Minimum changes required **before** remote backends: (1) models + Protocol + local adapter; (2) service layer owning lifecycle; (3) runner/`status` consume the service; (4) decisions on Q1, Q2, Q5; (5) the delegation seam in §3. Nothing else.

## 5. Blockers and open decisions

| ID | Blocker / decision | Needed from | Gates |
|---|---|---|---|
| B1 | Resolved 2026-11-09: scoped token and org-level API-token access confirmed by successful Jira reads | — | — |
| B2 | Atlassian: admin must enable API-token MCP auth; required granular scopes and rate limits not documented in fetched pages | Org admin / B1 probe | PR 2 headless Jira |
| B3 | Resolved: Linear catalog recorded in §2.3 | — | — |
| B4 | Resolved 2026-11-09: full write round trip on both providers | — | — |
| D5 | Decided 2026-11-09: interactive OAuth is deferred beyond PR 0. API credentials cover v1. This is a deliberate deviation from PR 0 task 4; OAuth becomes relevant for organizations that refuse API tokens | — | After PR 2 |
| D1 | Decided 2026-11-09: no transition table for the local backend; `set_status` stays permissive (Q1). PR 1's "reject invalid status transitions" test covers only unknown status values | — | — |
| D2 | Decided 2026-11-09: `FAILED` and other non-completed outcomes keep becoming `BLOCKED` in the local runner (Q2). Revisited 2026-11-09 during PR 6: every backend keeps `BLOCKED` for a failed run, so the status never differs by backend. A failed run stays distinguishable from a product blocker by its `failed` disposition in the run report and an `Execution failed:` reason. `BLOCKED` also stops any automatic repeat; `./ae-sdlc work recover <ID> --requeue` is the explicit retry | — | PR 6 |
| D3 | Decided 2026-11-09: `mcp` is a required dependency of the `ae_sdlc` package, added in PR 2 | — | PR 2 |
| D4 | Decided 2026-11-09: PR 5 must allow network calls made by `./ae-sdlc work` from every provider's agent sandbox, and remove the restrictions that block them. Scope is limited to that command (plan constraint 12). Revised 2026-11-09 during PR 5: no per-command or per-host network limit is imposed; Codex and Cursor sandboxes allow all hosts, filesystem isolation stays | — | PR 5 |

### Runbook to clear B1 (about 10 minutes)

```bash
mkdir -p -m 700 ~/.config/ae-sdlc
install -m 600 /dev/null ~/.config/ae-sdlc/dev.env
# In an editor, add LINEAR_API_KEY=..., and either ATLASSIAN_EMAIL=... + ATLASSIAN_API_TOKEN=...
# or ATLASSIAN_SERVICE_ACCOUNT_KEY=...  Never paste tokens into an agent chat.
(set -a; . ~/.config/ae-sdlc/dev.env; set +a; uv run --no-project --with mcp==2.3.0 tools/mcp_probe.py linear)
(set -a; . ~/.config/ae-sdlc/dev.env; set +a; uv run --no-project --with mcp==2.3.0 tools/mcp_probe.py jira)   # add --all for the flat list
# output: .ae-sdlc-runtime/probe/<provider>-tools.json  (gitignored; no tokens)
```
Send both JSON files back; the matrix in §2.3 is then filled with real tool names/schemas. Follow-up (needs a disposable project): one create → edit → comment → link → transition → list round trip per provider.

## 6. Files

Added: `tests/conftest.py`, `tests/test_local_work_store.py`, `tests/test_runner_lifecycle.py`, `tests/test_installer_and_runtime_boundary.py`, `tools/mcp_probe.py`, `docs/PR0_Feasibility.md`. Modified: `pyproject.toml` (pytest config only). No runtime, installer, skill, or CLI changes; wheel contents unchanged (`tools/` and `tests/` are outside `src/ae_sdlc`).
