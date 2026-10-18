# M6 — `cdd-progress` MCP + CLI + Author Guide

**Goal:** any plugin can post tasks, outputs, links, and metrics to the Progress panel with one tool call or one CLI line. Outside the GUI, the calls are no-ops so plugins stay portable to plain Claude Code.

**Depends on:** M3 (`progress.post` RPC, `ProgressEvent`).

---

## Step 1 — MCP server package **[build]**

Create `packages/progress-mcp/` (TypeScript, built to a single `dist/index.js`, runnable with `node`). Use `@modelcontextprotocol/sdk` stdio server.

Env: `CDD_GUI_PROGRESS_URL` (set by the GUI when spawning the agent; e.g. `http://127.0.0.1:<port>/progress`), `CDD_GUI_SESSION_ID`.

If either env var is missing → every tool returns `{ok:true, noop:true}` and does nothing. **This is the portability rule.**

Tools (all return `{ok:true}`):

| Tool | Input schema | Posts |
|------|--------------|-------|
| `progress_task` | `{ id: string, title: string, status: "pending"\|"running"\|"done"\|"failed", detail?: string }` | `task` |
| `progress_output` | `{ name: string, path: string, mime?: string }` | `output` (mime inferred from extension if omitted) |
| `progress_link` | `{ system: string, ref: string, url: string, title?: string }` | `link` |
| `progress_metric` | `{ id: string, label: string, value: string\|number }` | `metric` |
| `progress_status` | `{ state: "running"\|"awaiting_input"\|"failed"\|"idle", message?: string }` | `status` |
| `progress_tasks` | `{ tasks: Array<{id,title,status,detail?}> }` | one `task` per item (batch) |

`source` is taken from env `CDD_PLUGIN_ID` if set by the plugin's `.mcp.json`, else `"plugin"`.

Transport to GUI: HTTP POST JSON to `CDD_GUI_PROGRESS_URL` with body `ProgressEvent`. 2s timeout; on failure return `{ok:true, delivered:false}`. Never throw.

Tool descriptions (these are what the model reads, keep them exact):
- `progress_task`: "Report a step of a multi-stage task to the user's progress panel. Call with status 'running' when starting a step and 'done' when finished. Use a stable id per step."
- `progress_output`: "Register a file you produced so it appears under Outputs. Call once per final file, not for scratch files."
- `progress_link`: "Register an external record (Jira key, PR, incident) you created or looked up."
- `progress_metric`: "Report a headline number, e.g. '14 vulnerabilities found'."

---

## Step 2 — GUI endpoint **[grunt]**

In `apps/server`, add a tiny HTTP listener on `127.0.0.1` (random free port, stored at startup) with route `POST /progress`. Validate body against `ProgressEvent` (zod or Effect Schema, whatever the repo uses). Force `sessionId` to match `CDD_GUI_SESSION_ID` from a header `X-Session` (reject otherwise). Then `ProgressStore.post`.

When spawning each Agent SDK session, set env: `CDD_GUI_PROGRESS_URL`, `CDD_GUI_SESSION_ID`. Also inject the MCP server into every session's config as `cdd-progress` (so plugins don't need to declare it). Read the SDK docs for how to add an MCP server programmatically (`mcpServers` option).

**Verify:** `curl -X POST ... /progress` with a task event → panel updates.

---

## Step 3 — CLI shim **[build]**

Same package, second entry `dist/cli.js`, exposed as bin `cdd-progress`.

```
cdd-progress task <id> <status> <title...>
cdd-progress output <path> [--name NAME]
cdd-progress link <system> <ref> <url> [--title T]
cdd-progress metric <id> <label> <value>
cdd-progress status <state> [message...]
```

Same env rules; missing env → exit 0 silently. Also parse structured stdout lines so Bash-based plugins can emit without calling the CLI:

In the server's Bash tool `PostToolUse` handler (M3), scan stdout for lines matching
```
::progress <kind> <json>::
```
e.g. `::progress task {"id":"scan","title":"Scanning Snyk","status":"done"}::` and post them. Strip these lines from what the model sees? **No** — leave them; they're harmless.

---

## Step 4 — Author guide **[grunt]**

Write `docs/plugin-authors/progress.md` (this goes in the marketplace repo too):

```md
# Reporting progress from your plugin

The CDD GUI shows a Progress panel. Your plugin can post to it. Outside the GUI these calls do nothing, so it's safe to add everywhere.

## From a skill (SKILL.md)
Add to your skill instructions:
> For multi-step work, call `progress_task` with status "running" when you start each step and "done" when it finishes. Use short stable ids: "scan", "triage", "fix". When you produce a final file, call `progress_output`. When you create or look up a Jira/SNOW/PR/incident, call `progress_link`.

## From a script (Bash)
Print a line:
    ::progress task {"id":"scan","title":"Scanning Snyk","status":"running"}::
or use the CLI:
    cdd-progress task scan running Scanning Snyk

## Rules
- Max ~10 tasks per run. Group small steps.
- Only register final outputs, not scratch files.
- ids are stable per step; re-posting the same id updates it.
- Never put secrets in titles or details.
```

---

## Step 5 — Update 5 plugins **[parallel]** (one subagent each, Haiku)

For each, add the progress instructions paragraph to the relevant SKILL.md and (for Bash-based) `::progress` lines at step boundaries. Open a PR per plugin against the marketplace repo.

1. `cdd-github` — tasks: `search`, `ownership`
2. `cdd-snyk` — tasks: `fetch`, `summarise`; metric: `vulns_total`; output: report file
3. `cdd-sonarqube` — tasks: `fetch`, `summarise`; metric: `quality_gate`
4. `cdd-atlassian-knowledge-base` — link on every ticket created/read
5. `cdd-blazemeter` — tasks: `upload`, `run`, `report`; metric: `p95_ms`; output: report

---

## Step 6 — Marketplace CI lint **[grunt]**

Add a script `scripts/lint-progress.mjs` to the marketplace repo: for each plugin whose any SKILL.md mentions ≥3 numbered steps or the word "phase", warn if no `progress_task` mention. Non-blocking (warning only). Wire into existing CI.

**Verify (manual):** run cdd-snyk in the GUI → panel shows 2 tasks ticking, a metric, and the report under Outputs. Run the same plugin in terminal Claude Code → no errors.

---

## Step 7 — Review **[build]**

`/code-review`. Commit `M6 complete`.
