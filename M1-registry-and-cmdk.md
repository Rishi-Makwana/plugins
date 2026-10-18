# M1 — Plugin Registry + ⌘K

**Goal:** every plugin in the marketplace is indexed locally and reachable by intent in under a second. User presses `⌘K`, types what they want to do, presses Enter, plugin is attached to the current session.

**Not in scope:** chips, cards, auth status, progress panel.

---

## Step 1 — Contract types **[build]**

Create `packages/contracts/src/plugins.ts`:

```ts
export type PluginCategory =
  | "Platform" | "Search & Discovery" | "CDD Data Access" | "CDD Data Governance"
  | "Design & Specs" | "CI/CD" | "Compliance" | "Security" | "Git & GitHub"
  | "People & Identity" | "Claude & Meta";

export type PluginAuthType = "none" | "token" | "oauth" | "browser-session" | "cli-login";

export type PluginSafety = {
  readOnly: boolean;          // true if description/skills say read-only / never submits
  writes: string[];           // tool or command names that mutate external state
  envScope: string | null;    // e.g. "dev01", "sit", "non-prod", null = unrestricted
  prodBlocked: boolean;
};

export type PluginCommand = { name: string; description: string; filePath: string };
export type PluginSkill   = { name: string; description: string; filePath: string };
export type PluginMcp     = { name: string; command: string; args: string[]; env: Record<string,string> };

export type PluginDescriptor = {
  id: string;                 // dir name, e.g. "cdd-jira"
  name: string;
  version: string;
  category: PluginCategory;
  description: string;        // one line from plugin.json
  longText: string;           // concatenated README + SKILL.md bodies, for FTS only
  systems: string[];          // e.g. ["jira","confluence"]; see Step 3 for derivation
  commands: PluginCommand[];
  skills: PluginSkill[];
  mcpServers: PluginMcp[];
  triggers: string[];         // regex strings; empty in M1, filled in M2
  safety: PluginSafety;
  auth: PluginAuthType;
  dependsOn: string[];        // other plugin ids
  sourcePath: string;         // absolute path in marketplace cache
  updatedAt: string;          // ISO
};

export type PluginSearchHit = { plugin: PluginDescriptor; score: number; matchedOn: "name"|"description"|"body" };
```

Add to `packages/contracts/src/rpc.ts`:

```ts
"plugins.list":   { input: { category?: PluginCategory }; output: PluginDescriptor[] };
"plugins.search": { input: { q: string; limit?: number }; output: PluginSearchHit[] };
"plugins.get":    { input: { id: string }; output: PluginDescriptor | null };
"plugins.attach": { input: { sessionId: string; pluginId: string }; output: { ok: true } };
"plugins.detach": { input: { sessionId: string; pluginId: string }; output: { ok: true } };
"plugins.attached": { input: { sessionId: string }; output: string[] };
"plugins.recent": { input: { limit?: number }; output: PluginDescriptor[] };
"plugins.sync":   { input: {}; output: { added: number; updated: number; removed: number } };
```

Export from `packages/contracts/src/index.ts`.

**Verify:** `pnpm -F contracts typecheck` passes.

---

## Step 2 — Migration **[build]**

Create next migration in `apps/server/migrations/` (follow existing numbering):

```sql
CREATE TABLE plugins (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  version     TEXT NOT NULL,
  category    TEXT NOT NULL,
  description TEXT NOT NULL,
  systems     TEXT NOT NULL DEFAULT '[]',   -- JSON array
  commands    TEXT NOT NULL DEFAULT '[]',   -- JSON
  skills      TEXT NOT NULL DEFAULT '[]',   -- JSON
  mcp_servers TEXT NOT NULL DEFAULT '[]',   -- JSON
  triggers    TEXT NOT NULL DEFAULT '[]',   -- JSON
  safety      TEXT NOT NULL,                -- JSON PluginSafety
  auth        TEXT NOT NULL DEFAULT 'none',
  depends_on  TEXT NOT NULL DEFAULT '[]',   -- JSON
  source_path TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE plugins_fts USING fts5(
  id UNINDEXED,
  name,
  description,
  body,
  tokenize = 'porter unicode61'
);

CREATE TABLE plugin_usage (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  plugin_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  event      TEXT NOT NULL,   -- 'attached' | 'tool_call' | 'suggested' | 'accepted' | 'dismissed'
  at         TEXT NOT NULL
);
CREATE INDEX plugin_usage_plugin_at ON plugin_usage(plugin_id, at DESC);

CREATE TABLE session_plugins (
  session_id TEXT NOT NULL,
  plugin_id  TEXT NOT NULL,
  attached_at TEXT NOT NULL,
  PRIMARY KEY (session_id, plugin_id)
);
```

**Verify:** run migrations on a fresh DB; `sqlite3 db ".tables"` lists all four.

---

## Step 3 — MarketplaceSync service **[plan]** then **[build]**

Create `apps/server/src/services/MarketplaceSync.ts`.

Config (env vars with defaults):
- `CDD_MARKETPLACE_REPO` = `https://github.com/CBA-General/cdd-claude-code-plugins-marketplace`
- `CDD_MARKETPLACE_DIR` = `~/.cdd-gui/marketplace`
- `CDD_MARKETPLACE_SYNC_MINUTES` = `15`

Behaviour:
1. If dir missing → `git clone --depth 1`. Else `git pull --ff-only`. Use `child_process.execFile`, not shell.
2. Read `.claude-plugin/marketplace.json` at repo root. It lists plugins with `name` and `source` (relative path). If the file is missing, fall back to: every dir containing `.claude-plugin/plugin.json`.
3. For each plugin dir, build a `PluginDescriptor`:
   - `id` = dir name
   - `name`, `version`, `description` from `.claude-plugin/plugin.json`
   - `category`: from `plugin.json.category` if present; else from a static map `apps/server/src/services/pluginCategories.ts` keyed by id (seed it from the table in `docs/plan/plugins-table.md`); else `"Claude & Meta"`
   - `commands`: every `commands/**/*.md`; `name` = filename without `.md`; `description` = first line of frontmatter `description:` or first non-heading line
   - `skills`: every `skills/*/SKILL.md`; `name` = dir name; `description` = frontmatter `description:`
   - `mcpServers`: parse `.mcp.json` if present; each key → `{name, command, args, env}`
   - `longText`: `README.md` + all SKILL.md bodies + all command bodies, joined with `\n\n`, truncated to 20,000 chars
   - `systems`: lowercase words from `id` after `cdd-`, split on `-`, plus any from a static alias map (e.g. `atlassian-knowledge-base → ["jira"]`, `m365 → ["teams","outlook"]`, `pccs → ["argocd","grafana","prometheus"]`). Put the map in `pluginCategories.ts` too.
   - `safety`: 
     - `readOnly` = description or longText matches `/read-only|never submits|never shows secret/i`
     - `prodBlocked` = matches `/prod hard-blocked|prod.*blocked/i`
     - `envScope` = first match of `/\b(dev01|sit|non-prod|uat)\b/i` or null
     - `writes` = command names matching `/create|update|delete|submit|deploy|upload|send|post|schedule/i`
   - `auth`: `"browser-session"` if longText matches `/browser session|SSO/i`; `"token"` if `/token|API key/i`; `"cli-login"` if `/login|authenticate/i`; else `"none"`
   - `dependsOn`: `plugin.json.dependencies` keys if present, else `[]`
   - `triggers`: `[]`
4. Upsert into `plugins` and `plugins_fts` (delete FTS row by id then insert). Remove rows for ids no longer present.
5. Return `{added, updated, removed}`.
6. Expose `sync()` and `start()` (runs sync now, then every N minutes via `Effect.schedule`).

Service shape:

```ts
export class MarketplaceSync extends Context.Tag("MarketplaceSync")<MarketplaceSync, {
  sync: () => Effect.Effect<{added:number;updated:number;removed:number}, SyncError>;
  start: () => Effect.Effect<void, never, Scope.Scope>;
}>() {}
export const MarketplaceSyncLive: Layer.Layer<MarketplaceSync, never, Db | Config>;
```

Write `MarketplaceSync.test.ts` using a fixture dir `apps/server/test/fixtures/marketplace/` with two fake plugins (one with `.mcp.json`, one with commands + skills).

**Verify:** test passes; after `pnpm dev`, `plugins` table has ~60 rows; `SELECT count(*) FROM plugins_fts` matches.

---

## Step 4 — PluginRegistry service **[build]**

Create `apps/server/src/services/PluginRegistry.ts`.

```ts
export class PluginRegistry extends Context.Tag("PluginRegistry")<PluginRegistry, {
  list:   (category?: PluginCategory) => Effect.Effect<PluginDescriptor[]>;
  get:    (id: string) => Effect.Effect<PluginDescriptor | null>;
  search: (q: string, limit?: number) => Effect.Effect<PluginSearchHit[]>;
  recent: (limit?: number) => Effect.Effect<PluginDescriptor[]>;
  recordUsage: (pluginId: string, sessionId: string, event: UsageEvent) => Effect.Effect<void>;
}>() {}
```

`search(q)` algorithm:
1. Trim `q`. If empty → return `recent(8)` as hits with score 0.
2. Build FTS query: split on whitespace, each term → `term*`, join with space (implicit AND). Escape double quotes.
3. `SELECT id, bm25(plugins_fts, 10.0, 5.0, 1.0) AS score FROM plugins_fts WHERE plugins_fts MATCH ? ORDER BY score LIMIT ?` (column weights: name 10, description 5, body 1).
4. If zero rows, fall back to `LIKE '%q%'` on name and description.
5. Map to `PluginSearchHit`; `matchedOn` = `"name"` if `q` is a substring of name, else `"description"` if substring of description, else `"body"`.
6. Join with full descriptor from `plugins` table.

`recent(limit)` = plugins ordered by latest `plugin_usage.at` where event in (`attached`,`tool_call`), limit N.

Write `PluginRegistry.test.ts`: seed 3 plugins, assert search ranking (name match beats body match).

**Verify:** test passes.

---

## Step 5 — RPC handlers **[grunt]**

In the existing RPC router (`apps/server/src/rpc/`), add handlers for every method in Step 1. `plugins.attach` must:
1. Insert into `session_plugins`
2. `recordUsage(pluginId, sessionId, "attached")`
3. Call `AgentSessions.reconfigure(sessionId)` (Step 6)

**Verify:** `pnpm -F server typecheck` passes; calling `plugins.search` over WS from the devtools console returns hits.

---

## Step 6 — Agent SDK wiring **[plan]** then **[build]**

Find where the server creates Agent SDK sessions (grep for `claude-agent-sdk` / `query(`). Add `AgentSessions.reconfigure(sessionId)`:

1. Read `session_plugins` for the session.
2. For each plugin, resolve its `sourcePath`.
3. Rebuild the SDK session options so these plugin dirs are loaded. Check the SDK version in `package.json` and read its docs for the exact option name — it is one of: `plugins`, `pluginDirs`, `settingSources`, or passing `--plugin-dir` style args. **Do not guess; read `node_modules/@anthropic-ai/claude-agent-sdk/README.md` or the type defs.** Note what you used in a comment.
4. If the SDK cannot hot-reload plugins mid-session, restart the session's agent process preserving conversation history via the SDK's resume/continue option.
5. Also register `PostToolUse` hook: on every tool call, if the tool name belongs to an attached plugin's MCP or command, `recordUsage(pluginId, sessionId, "tool_call")`.

Write a manual test script `apps/server/scripts/attach-smoke.ts`: start a session, attach `cdd-github`, send "search CBA-General repos for pathfinder", assert a tool call from the plugin appears in the stream.

**Verify:** smoke script passes.

---

## Step 7 — `⌘K` palette **[build]**

Create `apps/desktop/src/components/plugins/CommandPalette.tsx`.

Spec:
- Base UI `Dialog`. Opens on `⌘K` (mac) / `Ctrl+K`. Closes on `Esc`.
- Single `<input>` autofocused, placeholder `Search plugins…`.
- Debounce 120ms → `rpc("plugins.search", {q, limit: 8})`.
- Empty query → `rpc("plugins.recent", {limit: 5})` with heading `Recent`.
- Row layout (one line, 40px tall):
  - 16px icon from `apps/desktop/src/components/plugins/categoryIcon.tsx` (one lucide icon per category, map provided below)
  - `name` (font-medium)
  - `description` (text-muted, truncate)
  - right side: category tag (text-xs), then safety badge: `RO` if readOnly, `envScope` if set, red dot if prodBlocked is false and writes.length > 0
- Keyboard: ↑↓ move, Enter = attach, `⌘Enter` = attach + close + focus editor.
- On attach: `rpc("plugins.attach", {sessionId, pluginId})`, then show a chip above the editor: `[icon] cdd-github ×` (click × = detach). Chips row component: `AttachedPlugins.tsx`, reads `plugins.attached`.
- No other UI. No sidebar.

Category → lucide icon:
```
Platform → Server
Search & Discovery → Search
CDD Data Access → Database
CDD Data Governance → GitBranch
Design & Specs → FileText
CI/CD → Workflow
Compliance → ClipboardCheck
Security → Shield
Git & GitHub → Github
People & Identity → Users
Claude & Meta → Sparkles
```

Mount `CommandPalette` once at app root. Mount `AttachedPlugins` directly above the Lexical editor.

**Verify (manual):**
- `⌘K`, type `jira` → `cdd-atlassian-knowledge-base` first, `cdd-ct-wins` second
- type `who owns repo` → `cdd-github` and `cdd-teamforms` in top 3
- type `cost` → `cdd-finops` first
- Enter on a result → chip appears, agent uses the plugin on next message

---

## Step 8 — Settings page: plugin list **[grunt]**

Create route `/settings/plugins` (TanStack Router). Table grouped by category, columns: name, description, version, safety badges, `Sync now` button at top calling `plugins.sync`. Read-only otherwise. This is the only place all 60 are listed.

**Verify:** page renders all plugins; Sync now updates counts.

---

## Step 9 — Review **[build]**

Run `/code-review` on `git diff main`. Fix anything flagged. Ensure no `any`, no console.log left.

Commit: `M1 complete`.
