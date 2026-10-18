# M3 — Progress Panel

**Goal:** a right-side panel, toggled by one button, showing Tasks, Outputs, and Used in this session. Fed automatically from Agent SDK events. Persists in SQLite so it rebuilds when the app reopens.

Reference: Claude.ai's own Progress panel (see `docs/plan/reference/progress-panel.png`). Sections: **Outputs** (file name + type), **Used in this session** (Uploads, Connectors, Web search, Memory with a compact summary on the right). We add **Tasks** at the top.

**Depends on:** M1 (for plugin ids in "Used").

---

## Step 1 — Contract **[build]**

Create `packages/contracts/src/progress.ts`:

```ts
export type TaskStatus = "pending" | "running" | "done" | "failed";

export type ProgressEvent =
  | { kind: "task";   sessionId: string; source: string; at: string; id: string; title: string; status: TaskStatus; detail?: string }
  | { kind: "output"; sessionId: string; source: string; at: string; id: string; name: string; path: string; mime: string; bytes?: number }
  | { kind: "link";   sessionId: string; source: string; at: string; id: string; system: string; ref: string; url: string; title?: string }
  | { kind: "status"; sessionId: string; source: string; at: string; state: "running"|"awaiting_input"|"awaiting_approval"|"failed"|"idle"; message?: string }
  | { kind: "metric"; sessionId: string; source: string; at: string; id: string; label: string; value: string|number }
  | { kind: "used";   sessionId: string; source: string; at: string; category: "plugin"|"upload"|"websearch"|"memory"|"file"; key: string; label: string };

// source = "agent" | "gui" | plugin id (e.g. "cdd-jira")

export type ProgressSnapshot = {
  sessionId: string;
  status: Extract<ProgressEvent, {kind:"status"}> | null;
  tasks:   Array<Extract<ProgressEvent, {kind:"task"}>>;     // ordered by first seen
  outputs: Array<Extract<ProgressEvent, {kind:"output"}>>;   // newest first, deduped by path
  links:   Array<Extract<ProgressEvent, {kind:"link"}>>;
  metrics: Array<Extract<ProgressEvent, {kind:"metric"}>>;   // latest per id
  used: {
    plugins:   string[];     // plugin ids
    uploads:   string[];     // file names
    websearch: string[];     // domains
    memory:    string[];     // memory keys/titles
    files:     string[];     // paths read
  };
};
```

RPC:

```ts
"progress.snapshot": { input: { sessionId: string }; output: ProgressSnapshot };
"progress.post":     { input: ProgressEvent; output: { ok: true } };          // used by M6
```

WS push event (server → client): `"progress.event"` carrying a `ProgressEvent`.

---

## Step 2 — Migration **[grunt]**

```sql
CREATE TABLE progress_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  kind       TEXT NOT NULL,
  source     TEXT NOT NULL,
  at         TEXT NOT NULL,
  payload    TEXT NOT NULL   -- full ProgressEvent JSON
);
CREATE INDEX progress_events_session ON progress_events(session_id, id);
```

---

## Step 3 — Reducer **[plan]** then **[build]**

Create `packages/contracts/src/progressReducer.ts` (pure, shared by server and client):

```ts
export function emptySnapshot(sessionId: string): ProgressSnapshot;
export function reduce(s: ProgressSnapshot, e: ProgressEvent): ProgressSnapshot;
```

Rules:
- `task`: upsert by `id`. Keep original position. Status transitions only forward (`pending→running→done|failed`); ignore backwards.
- `output`: dedupe by `path`; newer replaces older and moves to front. Cap 50.
- `link`: dedupe by `system+ref`.
- `status`: replace.
- `metric`: upsert by `id`.
- `used`: add `key` to the matching category set if absent; keep insertion order. `plugin` category uses `key` = plugin id.

Write `progressReducer.test.ts` covering each rule.

**Verify:** tests pass.

---

## Step 4 — ProgressStore service **[build]**

Create `apps/server/src/services/ProgressStore.ts`:

```ts
export class ProgressStore extends Context.Tag("ProgressStore")<ProgressStore, {
  post:     (e: ProgressEvent) => Effect.Effect<void>;      // insert row, update cached snapshot, push WS event
  snapshot: (sessionId: string) => Effect.Effect<ProgressSnapshot>;  // from cache or rebuild from rows
}>() {}
```

- In-memory `Map<sessionId, ProgressSnapshot>`. On miss, replay all rows for the session through `reduce`.
- Rate limit: if the same `(sessionId, kind, id)` arrives more than 10×/s, drop extras.
- `post` broadcasts `progress.event` to all WS clients subscribed to that session.

---

## Step 5 — Auto-capture from Agent SDK **[plan]** then **[build]**

In the Agent SDK session wrapper (same place as M1 Step 6), register hooks. Read the SDK docs for exact hook names; they correspond to Claude Code's `PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`.

Mapping (source = `"agent"` unless noted):

| SDK event | ProgressEvent |
|-----------|---------------|
| session start | `status running` |
| `TodoWrite` / task tool call | one `task` per item, `id` = item id or hash of title |
| `Write` / `Edit` tool with path under the session's output dir | `output` with name = basename, mime from extension |
| `Read` tool | `used file key=path` |
| `WebSearch` / `WebFetch` | `used websearch key=domain` |
| memory read tool | `used memory key=path label=title` |
| any tool whose name starts with `mcp__<server>__` where server belongs to an attached plugin | `used plugin key=pluginId`, source = pluginId |
| any Bash call whose command starts with a plugin's command name | same as above |
| tool result contains a URL matching a known system (jira, servicenow, github, pagerduty) | `link` with system + ref extracted (regexes in `apps/server/src/services/linkExtract.ts`) |
| agent asks a question / awaits permission | `status awaiting_input` or `awaiting_approval` |
| session stop | `status idle` |
| error | `status failed message=<err>` |

Uploads: when the GUI attaches a file to a message, the server posts `used upload key=filename`.

MIME from extension: md, zip, png, jpg, pdf, csv, json, html, ts, py → use `mime-types` package if present, else a 10-entry map.

**Verify:** run a session that writes 2 files and calls one plugin; `progress.snapshot` shows 2 outputs and the plugin under `used.plugins`.

---

## Step 6 — Panel UI **[build]**

Create `apps/desktop/src/components/progress/`:

- `ProgressPanel.tsx` — the panel
- `ProgressToggle.tsx` — single icon button in the top bar, shows a small dot when status is `awaiting_*`
- `useProgress.ts` — loads `progress.snapshot` on mount, applies `progress.event` pushes via the shared reducer

Layout (match the reference screenshot; width 320px; slides in from right; does not push the chat, overlays it):

```
Progress                                   ×
───────────────────────────────────────────
Tasks                      (only if ≥1 task)
  ○ Scan Snyk                      running
  ✓ Triage findings                done
  ○ Open PR                        pending

Outputs                    (only if ≥1)
  [doc] 05 preauth build plan        MD
  [zip] Preauth                      ZIP
  [img] Flagged                      PNG

Used in this session       (only if any)
  Plugins      cdd-github, cdd-snyk +2
  Uploads      IMG_3663.JPG, IMG_3664.JPG +3
  Web search   [favicon][favicon] +6
  Memory       Read · Github Digest, Ai Gui…
```

Rules:
- Section hidden if empty. Never show an empty heading.
- Task icons: `○` pending (muted), spinner running, `✓` done (green-ish muted), `✕` failed (red).
- Output row: icon by mime (lucide `FileText`, `Archive`, `Image`, `FileCode`, default `File`), name, extension uppercase right-aligned muted. Click → open file (existing file-open RPC) or reveal.
- Used rows: label left, compact value right, truncated with `+N`. Click a row → expands to full list inline.
- Links section (M4 will turn these into cards): for now a row per link: `[system icon] JIRA CDD-1234` → opens in side browser view.
- Status: if `awaiting_approval` or `awaiting_input`, show a 1-line banner at the top of the panel: "Waiting for you" / "Needs approval".
- No animations beyond the slide-in. No colours beyond muted/green/red.

Keyboard: `⌘⇧P` toggles the panel.

**Verify (manual):**
- Run a multi-step task → tasks tick live
- Write a file → appears under Outputs within 1s
- Close app, reopen the session → panel identical
- Empty session → panel shows only "Progress" header

---

## Step 7 — Review **[build]**

`/code-review`. Commit `M3 complete`.
