# Plugin Integration Plan

Native integration of the CDD plugin marketplace (~60 `cdd-*` Claude Code plugins) into the AI GUI.

## Design rules (non-negotiable)

1. The chat is the product. Nothing else is visible by default.
2. Plugins surface in exactly three places: `⌘K`, suggestion chips, the Progress panel.
3. Chips: max 3, only when a trigger fires.
4. Cards over text. One card per result. Collapsed by default.
5. Status lives on the thing (chip/card), never in a separate dashboard.
6. Settings is the only place the full list of 60 is ever shown.
7. Cold-start test: open the app, you see the chat box and the Progress toggle. Nothing else.

## Milestones

| # | File | Goal | Done when |
|---|------|------|-----------|
| M1 | `M1-registry-and-cmdk.md` | Index all plugins, search by intent, attach to session | 60 plugins indexed; `⌘K` attach works |
| M2 | `M2-suggestion-chips.md` | Auto-suggest plugins from message content | Jira key → correct chip; no trigger → no chips |
| M3 | `M3-progress-panel.md` | Live tasks/outputs/plugins-used panel, survives restart | Close + reopen, panel intact |
| M4 | `M4-native-cards.md` | 5 rich cards for Jira/SNOW/PR/PagerDuty/Vulns | Each card shows live state, opens source |
| M5 | `M5-approval-cards.md` | Preview + approve every write action | No write executes without a click |
| M6 | `M6-progress-mcp.md` | Plugins can post to the Progress panel | 5 plugins report tasks via MCP/CLI |
| M7 | `M7-guided-sessions.md` | Stepper UI for interview-style plugins | grill-me runs as a stepper |

Do them in order. Each depends on the previous.

## How to run a milestone with Claude Code

- One fresh session per **step**, not per milestone. `/clear` between steps.
- Prompt template: `Implement Step N of docs/plan/MX-*.md. Read only that file and the files it lists. Do not read the rest of the repo unless the step says so.`
- Models:
  - Steps marked **[plan]** → Opus, high effort
  - Steps marked **[build]** → Sonnet, medium effort
  - Steps marked **[grunt]** → Haiku, low effort
  - Steps marked **[parallel]** → spawn one subagent per bullet, Sonnet
- After each step: run the step's **Verify** block. Do not move on if it fails.
- Commit after each step with message `MX stepN: <short>`.

## Repo conventions (put in CLAUDE.md if not already)

```
Monorepo packages:
  apps/desktop      Electron 40 + React 19 + Vite 8 + Tailwind 4 + TanStack Router + Base UI
  apps/server       Node + Effect 4, SQLite via node:sqlite, migrations in apps/server/migrations
  packages/contracts  Shared TS types + WS RPC method definitions
  packages/retrieval  Library: node:sqlite FTS5, web-tree-sitter, optional sqlite-vec
  packages/retrieval-ui
Agent: Claude Code via @anthropic-ai/claude-agent-sdk
RPC: WebSocket, methods declared in packages/contracts/src/rpc.ts
Style: Effect services use Context.Tag + Layer. No classes in React. Tailwind only, no CSS files.
Tests: vitest. One test file per service/component.
```

## Naming

- Plugin IDs are the marketplace dir name, e.g. `cdd-jira`. Never rename.
- New Effect services: `apps/server/src/services/<Name>.ts`, exported as `<Name>` tag + `<Name>Live` layer.
- New React components: `apps/desktop/src/components/<feature>/<Name>.tsx`.
- New RPC methods: `namespace.verb`, e.g. `plugins.search`.

## Local paths

- Marketplace clone cache: `~/.cdd-gui/marketplace/`
- SQLite: whatever `apps/server` already uses. Add tables via migrations only.
