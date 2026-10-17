# INTEGRATE.md — read this first, then act

For the agent integrating this into the host desktop app. Everything below is decided. Do not re-verify what is marked verified, do not re-open decisions, do not ask the user about anything not listed under "Stop and ask only if".

## Read order (nothing else until needed)

1. This file.
2. `README.md` — integration steps 1–8 and the Phase 3/4 sections.
3. `packages/retrieval-ui/README.md` — only when you reach step 7 below.

Skip `PLAN.md` (history), `reference/` (only needed at step 8), `packages/retrieval/bench/` and `eval/` (only if the user asks for measurements).

## Verified — trust it, do not re-test

- `packages/retrieval`: 99 tests pass, `tsc --noEmit` clean, Node ≥ 24, native TypeScript type stripping.
- `packages/retrieval-ui`: 26 logic tests pass, demo builds, keyboard flow / focus return / 360 px layout checked in Chrome.
- Runs inside a packaged Electron 40.10.6 macOS app in `utilityProcess`: `node:sqlite` with FTS5, `sqlite-vec`, `web-tree-sitter` + grammar wasm.
- Agent SDK API (`@anthropic-ai/claude-agent-sdk` 0.3.302): `createSdkMcpServer({ name, tools })`, `tool(name, description, zodShape, handler)`, tool names appear to the model as `mcp__retrieval__search|def|read|recall`. Already used in `src/agent-tools.ts`.
- Index speed: ~3.3 s for 3.6k files, ~130 ms unchanged re-index, queries ~1 ms.

## Decisions already made — do not relitigate

- Index lives in its own SQLite file: `<userData>/retrieval/<workspace-hash>.db`. Not the app's main DB. Reason: separate `user_version` migration counters, separate driver (`node:sqlite`), rebuildable cache.
- Keyword search (FTS5) only in the first integration. Vectors (Phase 4) stay off until the user asks or keyword search demonstrably misses paraphrased questions. Do not install `@huggingface/transformers` or `sqlite-vec` in the host now.
- Wrap the library in the host's existing Effect service pattern. Copy the pattern of a neighbouring service; invent nothing new.
- Agent tools are in-process MCP (`createRetrievalMcpServer`), not a separate server.
- Indexing and watching run in the server process, never in the Electron main process or renderer.
- The `retrieval` package is copied in as-is. Adapt lint/format config to the host after tests pass, not before.

## Work order — do these in sequence, each with its check

1. Copy `packages/retrieval` and `packages/retrieval-ui` into the host monorepo. `npm ci --ignore-scripts` (flag required: tree-sitter grammar packages carry unneeded native install scripts). Check: `npm test` → 99 pass, 1 skipped; `npx tsc --noEmit` clean.
2. Token counter first (it is the baseline that proves the rest): mount `TokenMeter` in the chat header/footer, feed it `accumulateUsage()` over the SDK `result` messages. Check: visible per-turn and session numbers while chatting.
3. `RetrievalService` (Effect): acquires `openStore(dbPath)` + `createChunker(...)`, releases by closing the DB; exposes `indexWorkspace`, `search`, `def`, `readRange`, `recall`, `indexChatTurn`, `forgetSession`. Map thrown errors to the host's tagged errors; `readRange` path rejection is an expected error, not a defect. Check: a host test indexes `test/fixtures/repo` and `def('IndexService')` returns a hit.
4. Agent tools: pass `createRetrievalMcpServer({ db, root })` in `query()` options `mcpServers`, and add `RETRIEVAL_TOOL_NAMES` to `allowedTools`. Check: in a real session, `mcp__retrieval__search` appears in the init tool list and is called at least once on a "where is X" question about the workspace.
5. Freshness: after workspace open, `indexWorkspace` → `watchWorkspace` → `setInterval(() => watcher.requestRescan(), 60_000)`; `await watcher.close()` on workspace close and app quit. Check: edit a file, search within 2 s shows the new content.
6. Chat memory: after each completed turn call `indexChatTurn` for the user message and the assistant reply; add a "forget this conversation" action → `forgetSession`. Check: new session, `recall` tool finds a fact from the previous session.
7. GUI: RPC methods `retrieval.search|def|read|recall` in the contracts package (validate inputs with the host's schema library); mount `CommandPalette` at the app shell with a `RetrievalClient` built on those RPC calls; render `CitationChips` under assistant messages from `extractCitationsFromTurn`. Add the Tailwind `@source` line from the UI README. Check: ⌘K opens, Enter opens a file at the line; chips appear after a turn that used the tools.
8. Packaging: ship wasm via electron-builder `extraResources` to `Resources/wasm/` and pass `createChunker({ wasmDir: () => path.join(process.resourcesPath, 'wasm') })` when `app.isPackaged`; set `npmRebuild: false` for the grammar packages. Check: packaged app indexes its own workspace and `def` works.

## Do not

- Do not rewrite the library in a different style, split files, add abstractions, or add dependencies the host does not already have.
- Do not run `bench/run.ts` or `eval/run.ts` without the user's explicit OK; each run bills real model calls.
- Do not index anything outside the workspace root the user opened. Never the home directory.
- Do not commit `bench/*.local.json`, `eval/*.local.json`, `bench/results/`, or any file naming private repos. They are gitignored; keep it that way.
- Do not use the `tree-sitter-wasms` npm package (incompatible with web-tree-sitter 0.27).
- Do not run `git status` on a repo you must not modify without `--no-optional-locks` (plain `git status` rewrites `.git/index`).

## Gotchas already handled — just know them

- `search()` excludes chat chunks unless `kinds` includes `'chat'`; `recall()` is the chat search.
- `k < 1` clamps to 1; `k > 10` clamps to 10; `readRange` caps at 200 lines; snippets at 20 lines.
- Symlinks (files and dirs) are skipped by the indexer on purpose.
- macOS FSEvents may miss writes in the first moments after `watchWorkspace`; the `indexWorkspace`-then-watch order in step 5 covers it.
- Duplicate checkouts under one root (git worktrees, `file 2.ts` copies) produce duplicate hits. Scope the root; de-duplication is not built.
- Per-turn cost from the SDK is the difference between consecutive cumulative `total_cost_usd` values; `accumulateUsage` already does this.
- The chunker has grammars for TS/TSX/JS/Python/Rust/Go only. Other languages index as 60-line windows and `def` returns nothing for them.

## Stop and ask only if

1. The host's SQLite driver cannot be kept separate from `node:sqlite` (e.g. a build constraint forbids `node:sqlite`), or the host insists on one DB file.
2. The host's installed Agent SDK major version differs from 0.3.x and `createSdkMcpServer` / `tool` are missing or renamed.
3. Packaging on Windows or Linux fails for `.wasm` or native modules (never tested here).

Anything else: pick the option matching the host's existing code and continue.
