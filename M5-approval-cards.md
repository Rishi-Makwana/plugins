# M5 — Approval Cards

**Goal:** any plugin action that writes to an external system (create ticket, comment, deploy, send message, upload evidence) pauses and shows a preview card. Nothing executes until the user clicks Approve. Read-only plugins never prompt.

**Depends on:** M1 (safety metadata), M3 (status events, panel).

---

## Step 1 — Write-action registry **[grunt]**

Create `apps/server/src/services/writeActions.ts`. Explicit allow-list of tools that mutate. Patterns match against the full tool name (`mcp__<server>__<tool>`) or, for Bash-based plugins, against the command string.

```ts
export const WRITE_ACTIONS: Array<{ pluginId: string; match: RegExp; label: string; risk: "low"|"medium"|"high" }> = [
  { pluginId: "cdd-atlassian-knowledge-base", match: /create|update|transition|comment|assign/i, label: "Jira write", risk: "medium" },
  { pluginId: "cdd-ct-wins",         match: /create/i,                         label: "Create Win ticket", risk: "low" },
  { pluginId: "cdd-servicenow",      match: /create|update|close|comment/i,     label: "ServiceNow write", risk: "high" },
  { pluginId: "cdd-sdlchub",         match: /deploy|upload|create|submit/i,     label: "SDLC Hub write", risk: "high" },
  { pluginId: "cdd-blazemeter",      match: /start|run|create|delete/i,         label: "BlazeMeter run", risk: "medium" },
  { pluginId: "cdd-m365",            match: /send|schedule|create|reply/i,      label: "Teams / Calendar write", risk: "medium" },
  { pluginId: "cdd-github",          match: /\bgh (pr|issue) (create|comment|merge|close)|gh api .* -X (POST|PUT|PATCH|DELETE)/i, label: "GitHub write", risk: "medium" },
  { pluginId: "cdd-launchdarkly",    match: /toggle|update|create|delete/i,     label: "Feature flag change", risk: "high" },
  { pluginId: "cdd-kubectl",         match: /kubectl (apply|delete|scale|rollout|patch|edit)/i, label: "Kubernetes change", risk: "high" },
  { pluginId: "cdd-aws",             match: /aws .* (create|delete|put|update|terminate|stop|start)/i, label: "AWS change", risk: "high" },
  { pluginId: "cdd-identity",        match: /request|submit/i,                  label: "Access request", risk: "medium" },
  { pluginId: "cdd-marketplace-feedback", match: /create/i,                     label: "Create GitHub issue", risk: "low" },
  { pluginId: "cdd-bounty",          match: /create|update|close/i,             label: "Bounty change", risk: "low" },
  { pluginId: "cdd-artifacts",       match: /publish|upload/i,                  label: "Publish page", risk: "low" },
  { pluginId: "cdd-engineering-autopilot", match: /.*/,                         label: "Autopilot step", risk: "medium" }, // gates handled in M7
];
```

Plugins NOT listed never prompt. `cdd-wasp` drafts only (never submits) → not listed. `cdd-vault`, `cdd-wiz`, `cdd-opsindex`, `cdd-cmdm-database`, `cdd-teamforms` are read-only → not listed.

---

## Step 2 — Contract **[grunt]**

`packages/contracts/src/approvals.ts`:

```ts
export type ApprovalRequest = {
  id: string;
  sessionId: string;
  pluginId: string;
  label: string;               // from WRITE_ACTIONS
  risk: "low"|"medium"|"high";
  toolName: string;
  summary: string;             // one line, generated (Step 4)
  preview: Array<{ label: string; value: string }>;  // e.g. Project: CDD, Summary: "...", Assignee: ...
  raw: unknown;                // full tool input, shown under "Details"
  createdAt: string;
  state: "pending"|"approved"|"rejected"|"expired";
};
```

RPC:
```ts
"approvals.list":    { input: { sessionId: string }; output: ApprovalRequest[] };
"approvals.resolve": { input: { id: string; decision: "approve"|"reject"; note?: string }; output: { ok: true } };
```
WS push: `"approvals.request"` with an `ApprovalRequest`.

Migration:
```sql
CREATE TABLE approvals (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, plugin_id TEXT NOT NULL,
  label TEXT NOT NULL, risk TEXT NOT NULL, tool_name TEXT NOT NULL,
  summary TEXT NOT NULL, preview TEXT NOT NULL, raw TEXT NOT NULL,
  created_at TEXT NOT NULL, resolved_at TEXT, state TEXT NOT NULL, note TEXT
);
```

---

## Step 3 — Permission hook **[plan]** then **[build]**

In the Agent SDK session wrapper, register a `PreToolUse` hook (or the SDK's `canUseTool` / permission callback — read the SDK docs for the current name and whether it supports async deferral).

Logic:
1. Determine `pluginId` for the tool (same lookup as M3 Step 5).
2. Find a `WRITE_ACTIONS` entry where `pluginId` matches and `match.test(toolName)` or, for Bash, `match.test(command)`.
3. No match → allow immediately.
4. Match →
   - Build `ApprovalRequest` (Step 4), insert row, broadcast `approvals.request`
   - Post `ProgressEvent status awaiting_approval message=<label>`
   - **Block** the hook until `approvals.resolve` arrives or 10 minutes pass (then `expired`). Use an Effect `Deferred` keyed by approval id in an in-memory map.
   - On approve → return allow. On reject/expire → return deny with reason `"User rejected: <note>"` so the agent sees it and can adapt.
   - Post `status running` after resolution.
5. If the SDK permission API cannot block asynchronously, fall back to: deny with a message `"APPROVAL_REQUIRED:<id>"`, and on approve re-invoke the tool by sending a synthetic user message `"Approved <id>, proceed."` Document which path was used in a code comment.

Write a test with a fake tool call for `cdd-servicenow` `create_incident` asserting the hook blocks, then resolves on approve.

**Verify:** test passes.

---

## Step 4 — Preview builder **[build]**

`apps/server/src/services/approvalPreview.ts`:

```ts
export function buildPreview(pluginId: string, toolName: string, input: unknown): { summary: string; preview: Array<{label:string;value:string}> };
```

Rules:
- If `input` is an object: take up to 6 top-level string/number fields, prefer keys in this order: `project, summary, title, description, short_description, assignee, priority, state, status, channel, to, subject, body, message, name, environment, namespace`. Label = key humanised (`short_description → Short description`). Truncate values at 140 chars.
- If Bash: `summary` = first 100 chars of the command, preview = `[{label:"Command", value: command}]`.
- `summary` = `${label}: ${first preview value}`.

---

## Step 5 — Approval card UI **[build]**

`apps/desktop/src/components/approvals/ApprovalCard.tsx`:

- Rendered inline in the chat at the point the agent paused, AND as the banner in the Progress panel (M3 "Needs approval").
- Layout:
  ```
  [Shield icon]  Jira write · medium risk                       pending
  Create issue: "Add retry to PPD client"
  ─────────────────────────────────────
  Project     CDD
  Summary     Add retry to PPD client
  Assignee    rmakwana
  ▸ Details (raw JSON, collapsed)
  ─────────────────────────────────────
  [ Reject ]                      [ Approve ]
  ```
- Risk pill: low→neutral, medium→warn, high→bad.
- Approve = primary button. Reject opens a 1-line optional note input then confirms.
- After resolve: card collapses to one line `✓ Approved · Jira write · 11:42` or `✕ Rejected`. Not removable.
- Keyboard: when a card is pending and the editor is focused, `⌘⏎` approves, `⌘⌫` rejects. Show these hints in the card footer.
- Native OS notification when a request arrives and the window is not focused: title `Needs approval`, body = summary. Click → focus window.

Settings (`/settings/plugins`): "Auto-approve low-risk actions" toggle, default off. When on, `risk:"low"` requests resolve as approved immediately but still write an `approvals` row and a collapsed card.

**Verify (manual):**
- Ask cdd-servicenow to create an incident → card appears, agent waits
- Approve → incident created, card collapses with ✓
- Reject with note "wrong CI" → agent replies acknowledging the note
- Ask cdd-wiz to query → no card
- Turn on auto-approve low → cdd-ct-wins create does not prompt

---

## Step 6 — Review **[build]**

`/code-review`. Pay attention to: hook never deadlocks on session close (resolve all pending as `expired` on session end). Commit `M5 complete`.
