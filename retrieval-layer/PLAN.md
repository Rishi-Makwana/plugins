# Retrieval Layer Plan — SQLite-backed code/doc/chat search for the AI GUI

Status: plan only, not started. Written 2026-10-17.

## Goal

One sentence: the agent (Claude Code via Agent SDK) and the GUI can find the right passage in the user's workspace and chat history in one call, returning `path:start-end` snippets instead of whole files.

Done means:
- `search`, `def`, `read`, `recall` tools exist as in-process MCP tools and are used by the agent on a test task.
- Token count per turn on an unfamiliar private repo drops measurably vs. baseline (target: 2x fewer output tokens on a 5-task benchmark; xerj's study reports 2.7x vs grep).
- Editing a file and re-running `search` within 2s returns the new content (freshness).

Out of scope for v1: PDFs, logs, CSV, Elasticsearch-compatible API, ANN index, cross-repo "reference coding".

## Stack assumptions (from the user's description, not verified in code)

Electron 40, Node server using Effect 4, SQLite with migrations, WebSocket RPC with shared contracts package, Claude Agent SDK, TypeScript throughout.

## Architecture

```
renderer ──WS RPC──> node server
                        ├── IndexService (Effect service, owns SQLite tables)
                        │     ├── Walker + Watcher (chokidar, content hash per file)
                        │     ├── Chunker (web-tree-sitter, falls back to line windows)
                        │     ├── Embedder (optional, Transformers.js; absent in v1)
                        │     └── Ranker (FTS5 BM25 → later hybrid RRF)
                        └── Agent SDK session
                              └── in-process MCP server exposing search/def/read/recall
```

Everything lives behind `IndexService`. If SQLite stops being enough, only this service changes.

## Schema (migration 00X_retrieval.sql)

```sql
create table indexed_files (
  path text primary key,
  hash text not null,
  mtime integer not null,
  indexed_at integer not null
);

create table chunks (
  id integer primary key,
  path text not null,
  kind text not null,            -- 'code' | 'doc' | 'chat'
  symbol text,                   -- function/class name when known
  symbol_kind text,              -- 'fn' | 'class' | 'method' | 'type' | null
  start_line integer not null,
  end_line integer not null,
  text text not null,
  session_id text                -- chat chunks only
);
create index chunks_path on chunks(path);
create index chunks_symbol on chunks(symbol);

create virtual table chunks_fts using fts5(
  text, symbol, path,
  content='chunks', content_rowid='id',
  tokenize='unicode61 tokenchars ''_'''
);
-- triggers keep chunks_fts in sync with chunks (standard FTS5 external-content pattern)
```

Vector table deferred to Phase 4:

```sql
create virtual table chunks_vec using vec0(chunk_id integer primary key, embedding float[384]);
```

## Tool contract (the product surface — change with care)

| Tool | Input | Output | Cap |
|---|---|---|---|
| `search` | `{ query, k?, kinds?, pathPrefix? }` | `[{ path, startLine, endLine, symbol, score, snippet }]` | k ≤ 10, snippet ≤ 20 lines |
| `def` | `{ symbol }` | `[{ path, line, signature }]` | ≤ 5 |
| `read` | `{ path, startLine, endLine }` | `{ text }` | ≤ 200 lines per call |
| `recall` | `{ query, k? }` | same shape as `search`, `kind='chat'` only | k ≤ 10 |

Tool descriptions must state "prefer this over Grep/Read for files inside the workspace". Without that line the agent keeps using its built-in tools.

Same contract is exported through the WS RPC contracts package so the GUI command palette uses it too.

## Phases (ordered by what can kill the plan)

### Phase 0 — Spike the risky seams (1 day)
1. Load `sqlite-vec` in the packaged Electron build on macOS and Windows. Record pass/fail per OS.
2. Load `web-tree-sitter` + the TypeScript grammar `.wasm` from the packaged app's resources path.
3. Create the FTS5 table through your migration runner; insert 1,000 fake chunks; run one BM25 query.
Check: all three run from a packaged build, not only `vite dev`. If `sqlite-vec` fails, the plan still proceeds — vectors are Phase 4 and optional.

### Phase 1 — Index and FTS search (3–4 days)
1. `IndexService.indexWorkspace(root)`: walk, respect `.gitignore`, skip binaries and files > 1 MB, hash content, upsert `indexed_files`.
2. Chunker: tree-sitter for TS/JS/Python/Rust/Go → one chunk per top-level function/class/method, with `symbol` and `symbol_kind`. Unsupported languages and markdown → sliding window of 60 lines, 10-line overlap.
3. `search` over `chunks_fts` with `bm25()`, filtered by `kinds`/`pathPrefix`.
4. `def` by exact `symbol` match, then prefix match.
Check: index this app's own repo; `def("IndexService")` returns the right file and line; `search("websocket auth token")` returns the connect handler in top 3. Write these as tests against a fixture repo.

### Phase 2 — Agent integration (2 days)
1. Register the four tools with the Agent SDK's in-process MCP server helper (verify exact API name in the installed SDK version).
2. Run a 5-task benchmark on an unfamiliar repo with tools on vs. off. Record output tokens from the SDK result messages.
Check: tools are actually called (log tool-use events); tokens drop. If the agent ignores the tools, fix the tool descriptions before touching ranking.

### Phase 3 — Freshness and chat memory (2–3 days)
1. chokidar watcher → debounce 300 ms → re-hash → re-chunk only changed files; delete chunks for removed files.
2. On each completed agent turn, write the user message and assistant message as `kind='chat'` chunks with `session_id`.
3. `recall` tool.
Check: edit a file, call `search` within 2s, see new content. Ask "what did we decide about X" in a new session and get the old turn back.

### Phase 4 — Vectors and hybrid rank (optional, 3–4 days)
Only if Phase 2 benchmark shows FTS misses on paraphrased queries.
1. Embedder via Transformers.js (`Xenova/bge-small-en-v1.5` or a code model; 384 dims). Index-time only, batched, in the server process.
2. Hybrid: reciprocal rank fusion of FTS rank and vec distance, `k=60`.
Check: a held-out set of 20 paraphrased queries; hybrid must beat FTS-only on recall@5 or it is reverted.

### Phase 5 — GUI surfaces (2–3 days)
1. Command palette: search box calling `search` over RPC, results as `path:line`, click to open.
2. Citation chips under assistant messages from tool results (`path:start-end`), click to open at line.
3. Token-per-turn counter (build this first if possible — it is the baseline for the whole plan).

## Risks

- **Stale index after edits** — silent, trust-destroying. Mitigation: watcher + hash is in Phase 3, not later; show "indexing…" state in UI.
- **Agent ignores custom tools** — Phase 2 check catches it; fix is prompt/description work, not code.
- **Native modules in packaged Electron** (`sqlite-vec`, ONNX for Transformers.js) — Phase 0 spike; fallback is FTS-only.
- **Large workspaces** — brute-force vec search slows past ~100k chunks (inferred from sqlite-vec docs, not benchmarked). FTS5 is fine far beyond that. Cap indexing to the workspace root the user selected; no home-directory indexing.
- **Ranking tuning is unbounded** — time-box; ship FTS-only if benchmark is met.

## Decisions recorded

- SQLite over xerj sidecar: one store, one migration story, TypeScript throughout. Revisit if users need PDFs/logs/CSV at scale.
- FTS-first, vectors optional: xerj's own default embedder is lexical; symbols + BM25 likely carry most of the gain for code. Revisit after Phase 2 benchmark.
- web-tree-sitter (WASM) over native tree-sitter: avoids per-Electron-version rebuilds.

---

# Appendix — Faster UI rendering (separate workstream)

Not measured on your app; these are the standard hotspots for this exact stack. Profile first (React DevTools Profiler + Chrome Performance tab) and fix only what shows up.

1. **Streaming markdown re-parse.** Most common cause of jank in chat UIs: every token re-parses and re-renders the whole message. Fix: render completed blocks as memoized components keyed by block index; render only the trailing incomplete block as plain text; flush updates with `requestAnimationFrame` or a 30–50 ms throttle instead of per token.
2. **WebSocket message storm.** One RPC message per token means one React state update per token. Batch on the server: coalesce tokens into frames every ~30 ms. Fewer messages, fewer renders.
3. **xterm.js instances.** One live terminal per running tool is fine; a terminal per historical tool call is not. Convert finished output to static `<pre>` text and dispose the xterm instance.
4. **Lexical in the message list.** Lexical is an editor; use it for the composer only. Render historical messages with a plain markdown renderer.
5. **LegendList configuration.** Set `estimatedItemSize` close to real median height, enable `recycleItems`, and keep message components memoized with stable keys. Variable-height streaming items should update size via the list's measurement API, not by remounting.
6. **Electron-specific.** Keep heavy work (parsing, indexing, embeddings) in the server process, never in the renderer. Enable `backgroundThrottling: false` only for the window that streams.
7. **React 19 compiler.** If not already enabled, the React Compiler removes most manual `useMemo`/`useCallback` work; verify it is on in the Vite config.

Measure: target < 16 ms per frame while streaming a 2,000-line response; check with the Performance panel's frame chart.

## Phase 0 results (2026-10-17, macOS arm64 only)

All checks pass in Electron 40.10.6 (bundled Node 24.15.0), both `electron .` dev mode and packaged `--dir` build, run in `utilityProcess`.

- SQLite: `better-sqlite3` 13.0.3 and `node:sqlite` both pass FTS5 + bm25 and sqlite-vec 0.1.9 load + KNN.
- sqlite-vec path in packaged app: `sqliteVec.getLoadablePath().replace(/app\.asar([\\/])/, 'app.asar.unpacked$1')` with `asarUnpack: ['**/*.node','**/*.dylib']`.
- Tree-sitter: web-tree-sitter 0.27.1 + `tree-sitter-typescript@0.23.2` wasm; 18 correct chunks on sample; 5,016 lines parse ~22 ms, chunk ~2 ms; init ~11–18 ms.
- Wasm shipped via electron-builder `extraResources` to `Contents/Resources/wasm/`.
- `tree-sitter-wasms@0.1.13` is incompatible with web-tree-sitter 0.27 (dylink error); use the grammar package's own wasm or `@vscode/tree-sitter-wasm`.
- Not tested: Windows, Linux, code-signed/notarized build, wasm loaded from inside asar.
- Known harness bug: treesitter/main.js `finish` runs twice (message then exit), printing a spurious `ok:false`; exit code reflects the first, real result.

## Phase 1 results (2026-10-17)

Package: `retrieval/` (plain TS, node:sqlite, web-tree-sitter). `npm test`: 52/52 pass; `tsc --noEmit` clean.
- Acceptance (real chunker, fixture repo): `def('IndexService')` correct; `search('websocket auth token')` top 3 has connect handler; unchanged re-index writes 0 files.
- Real private repo (3,628 files): full index 3.3 s, 16,686 chunks; unchanged re-index 130 ms; queries 0.4–1.1 ms.
- Choices to revisit: k<1 clamps to 1; overlapping hits keep the inner one regardless of score; all symlinks skipped; IndexStats.chunks is store total.
- Known gap: duplicate checkouts (git worktrees / copied folders) under one root produce duplicate hits.

## Phase 3 results (2026-10-17)

`npm test`: 84/84 pass, stable over repeated runs; `tsc --noEmit` clean. macOS only; Linux/Windows fs.watch untested.
- Watcher (`src/watcher.ts`): stdlib `fs.watch` recursive, 300 ms per-path debounce, one serial queue, `.gitignore` change → full rescan, ignored paths never write, `close()` flushes pending edits. Edit → searchable well under 2 s in tests.
- macOS caveats: writes right after `watch()` can be missed (FSEvents start-up lag) and events from just before start are replayed. Run one `indexWorkspace` after starting the watcher and call `watcher.requestRescan()` on an interval (e.g. 60 s) as the safety net; it uses the same queue, so writes stay serial.
- Chat memory (`src/chat.ts`): migration 2 adds `role`, `created_at`, index on `session_id`. `indexChatTurn` (idempotent per session/turn/role), `forgetSession`, `recall`. Turn index is stored in start_line/end_line; chat path is virtual `chat/<sessionId>`.
- `search()` excludes chat unless `kinds` includes `'chat'`. New agent tool `mcp__retrieval__recall`.
