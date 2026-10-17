# Retrieval layer — handoff for integration

**If you are an AI coding agent: read this whole file before touching code.** It tells you what this is, what is proven, what is not, and how to integrate it into the host app without breaking anything.

## What this is

A local code/doc search layer for a desktop AI app. It indexes a workspace folder into SQLite and answers:

- `search(query)` → ranked passages as `path:startLine-endLine` + a ≤20-line snippet (keyword search: SQLite FTS5 + bm25, identifier-aware).
- `def(symbol)` → go-to-definition (`path:line` + signature line).
- `readRange(path, start, end)` → exact lines, ≤200, path-escape-safe.

Purpose: give the Claude agent (via Claude Agent SDK) and the GUI a way to fetch exact passages instead of grepping and reading whole files, cutting tokens per turn. Full design and roadmap: `PLAN.md` (Phases 0–1 done, 2–5 not started).

## The host app this was built for

As described by the user (this package has NOT seen the host repo — verify each item there):

- Electron 40 desktop shell, React 19 + Vite 8 + Tailwind 4 + TanStack Router + Base UI frontend
- Node server built with **Effect 4**, talking to the renderer over **WebSocket RPC** with shared types in a **contracts package**
- **SQLite with migrations** for app storage
- Agent: **Claude Code via the Claude Agent SDK**
- TypeScript everywhere

## What is in this folder

```
packages/retrieval/       the library — plain TypeScript, no framework, ESM
  src/types.ts            shared contract (types + size limits) — the public API shape
  src/chunker.ts          web-tree-sitter chunker: TS/TSX/JS/Python/Rust/Go symbols + window fallback
  src/store.ts            openStore(), schema migration, identifierTerms()
  src/indexer.ts          indexWorkspace(), indexFile(), removeFile() — gitignore-aware, hash-incremental
  src/search.ts           search(), def(), readRange()
  src/index.ts            public exports
  test/                   52 tests (node:test), fixtures included
reference/electron-spikes Phase 0 proof that this runs in a packaged Electron 40 app (copy the packaging config, not the code)
PLAN.md                   design, schema, tool contract, phase plan, Phase 0 + 1 results
```

## Proven vs not proven

Observed (ran it):
- 52/52 tests pass, `tsc --noEmit` clean (Node 26, TypeScript 7).
- In Electron 40.10.6 (bundled Node 24.15.0), dev **and** packaged macOS arm64 build, inside `utilityProcess`: `node:sqlite` FTS5 works; web-tree-sitter + grammar wasm load and parse.
- Real repo of 3,628 files: full index 3.3 s, unchanged re-index 130 ms, queries 0.4–1.1 ms.

Not proven:
- Windows / Linux, signed + notarized builds.
- The full `retrieval` package running inside packaged Electron (only its two building blocks were tested there).
- Anything about the host repo — every integration step below is a recommendation to check against the real code.
- Token savings for the agent (that is Phase 2's benchmark).

## Requirements

- Node ≥ 24 (uses built-in `node:sqlite` and native TypeScript type stripping; erasable syntax only, `.ts` import extensions).
- Install with **`npm ci --ignore-scripts`**. The `tree-sitter-<lang>` packages have native install scripts that are not needed — only their prebuilt `.wasm` files are used. Without `--ignore-scripts`, install may try node-gyp builds and fail.
- Dependencies: `web-tree-sitter` 0.27.1, `tree-sitter-{typescript,javascript,python,rust,go}`, `ignore`. Do **not** use the `tree-sitter-wasms` package — its wasm files are incompatible with web-tree-sitter 0.27 (`getDylinkMetadata` error).

Verify the copy works before integrating:

```sh
cd packages/retrieval
npm ci --ignore-scripts
npm test          # expect 52 pass, 0 fail
npx tsc --noEmit
```

## Usage

```ts
import { openStore, indexWorkspace, search, def, readRange, createChunker } from './packages/retrieval/src/index.ts'

const db = openStore('/path/to/retrieval.db')   // or ':memory:'
const chunker = createChunker()                   // packaged Electron: pass wasmDir, see below
const stats = await indexWorkspace(db, workspaceRoot, chunker)

search(db, { query: 'websocket auth token', k: 8, kinds: ['code'], pathPrefix: 'src/' })
def(db, 'IndexService')
readRange(workspaceRoot, 'src/server.ts', 10, 40)
```

All functions take a `DatabaseSync` from `node:sqlite`. No globals, no classes, no framework.

## Integration steps (in order)

Work in the host repo on a new branch. After each step, run the host's own typecheck/tests and this package's tests.

### 1. Add the package
Put `packages/retrieval` into the host monorepo following its existing package layout (workspace name, tsconfig extends, lint config). Keep the source as-is first; adapt style only after tests pass in the new location. If the host's build cannot import `.ts` extension paths, adjust the imports and tsconfig to match the host's convention.

### 2. Storage: use a separate database file (recommended)
Open the index in its **own** file, e.g. `<userData>/retrieval/<workspace-hash>.db`, not the app's main SQLite DB. Reasons:
- `migrate()` tracks its version with `PRAGMA user_version`. The host's migration runner may use the same pragma — sharing one file can corrupt both migration histories.
- The index is derived, rebuildable cache. A separate file can be deleted on corruption or schema change without touching user data.
- The library uses `node:sqlite`. The host may use a different driver (better-sqlite3, an Effect SQL client). Separate files mean no driver conflict.
Only fold it into the main DB if the host has a strong reason, and then port the migration SQL in `src/store.ts` into the host's migration system and drop `migrate()`.

### 3. Wrap in an Effect service
The library is synchronous DB calls plus async parsing. Wrap it as one service following **the host's existing Effect 4 service pattern** (look at how other services in the server are defined and provided; do not invent a new pattern). The service should own: the db handle (opened in acquire, closed in release), the chunker instance, and methods `indexWorkspace`, `search`, `def`, `readRange`. Map thrown errors to the host's tagged error types. Treat `readRange` path errors as expected (user/agent input) failures, not defects.

### 4. Expose over the WebSocket RPC
Add request/response schemas to the contracts package mirroring `src/types.ts` (`SearchOptions`, `SearchHit`, `DefHit`, `IndexStats`). Validate inputs at the boundary with the host's schema library. Add RPC methods `retrieval.search`, `retrieval.def`, `retrieval.read`, `retrieval.indexWorkspace` (stream or poll progress if the host's RPC supports it).

### 5. Expose to the agent as in-process MCP tools
Register tools on the Agent SDK session using the SDK's in-process MCP server helper (believed to be `createSdkMcpServer` + `tool()` — **verify the exact API in the installed SDK version**). Tool contract and caps are in `PLAN.md` → "Tool contract". Tool descriptions must tell the model to prefer them over built-in Grep/Read for files inside the workspace, or it will keep using its own tools. Starting point:

- `search`: "Search the current workspace by keywords or identifiers. Returns ranked passages with file path and line range. Prefer this over Grep/Glob/Read for finding code or docs in this workspace; then call read for the exact lines you need."
- `def`: "Find where a function, class, method or type is defined. Returns file path, line and signature."
- `read`: "Read an exact line range (max 200 lines) from a workspace file. Use after search or def instead of reading whole files."

### 6. Electron packaging
- `node:sqlite` is built into Electron's Node — nothing to unpack.
- The `.wasm` files must be real files on disk. Ship them with electron-builder `extraResources` into `Resources/wasm/` and pass `createChunker({ wasmDir: () => path.join(process.resourcesPath, 'wasm') })` when `app.isPackaged`. Files needed: `web-tree-sitter.wasm` (from `web-tree-sitter`) and `tree-sitter-<lang>.wasm` for each language (from each `tree-sitter-<lang>` package; `tree-sitter-typescript` holds both `typescript` and `tsx`). See `reference/electron-spikes/treesitter/package.json` → `build`.
- Keep `tree-sitter-*` grammar packages out of native rebuild (electron-builder `npmRebuild: false` or equivalent), otherwise it tries to node-gyp the unused native bindings.
- Run indexing in the server process (ideally an Electron `utilityProcess`), never in the renderer or main process.

### 7. Choose the workspace root
Index only the folder the user opens as the workspace. Never index home or system directories. Known gap: duplicate checkouts under one root (git worktrees, copied folders) return duplicate hits — scope the root, or add de-duplication later.

### 8. Verify end to end
- This package's tests pass in the host repo.
- From the packaged app: index the host repo itself; `def('<a real class in the host>')` returns the correct file and line; a natural-language `search` about a known feature has the right file in the top 3.
- Re-index after editing one file: only that file is re-indexed (`IndexStats.filesIndexed === 1`).

## Decisions left to the host / user

- `k < 1` currently clamps to 1 (returns one hit). Change to `[]` if preferred.
- Overlapping hits in the same file keep the inner (smaller) range regardless of score.
- All symlinks are skipped (files and dirs) to prevent escaping the root.
- `IndexStats.chunks` is the store total, not the count written in that run.

## Not in this package yet (next phases, see PLAN.md)

- Phase 2: agent tools + 5-task token benchmark (tools on vs off).
- Phase 3: file watcher for freshness, chat history indexing, `recall` tool.
- Phase 4: optional vectors (`sqlite-vec`, proven loadable in Phase 0) + hybrid ranking — only if keyword search misses paraphrased queries.
- Phase 5: GUI command palette, citation chips, token-per-turn counter.

UI rendering performance is a separate task: see `UI-RENDERING.md`.
