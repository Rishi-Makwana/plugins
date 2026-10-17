# retrieval-ui

Phase 5 of the retrieval layer: drop-in React pieces (command palette, citation chips, token meter) plus the pure logic behind them. Built without access to the host repo; every host-facing step below is a recommendation to verify against the real code.

Stack: React 19, TypeScript, Tailwind 4 utility classes, `@base-ui/react` 1.9.0. No router, no transport code, no other UI libraries.

## Pieces

| Piece | File | What it does |
|---|---|---|
| `parseCitations(toolName, text)` | `src/logic/citations.ts` | Parses `mcp__retrieval__search/def/read` text output into `{ path, startLine, endLine?, symbol? }[]`. Deduped. `recall` and `chat/<id>` virtual paths yield nothing. |
| `extractCitationsFromTurn(messages)` | same | Pairs `tool_use` and `tool_result` blocks (by id) in Agent SDK message objects; skips error results and non-retrieval tools. |
| `accumulateUsage(results)` / `formatTokens(n)` | `src/logic/usage.ts` | Per-turn and session token/cost totals from SDK `result` messages. |
| `createPaletteController` | `src/logic/palette.ts` | Debounce (120 ms), stale-response guard, `@`/`#` prefix modes, index wrap, safe highlight segments. |
| `CommandPalette` | `src/components/` | Cmd/Ctrl+K dialog with an ARIA combobox + listbox. |
| `CitationChips` | `src/components/` | Row of real buttons `file.ts:10–42`, tooltip with full path, "+N more" collapse. |
| `TokenMeter` | `src/components/` | Compact in/out/cache/cost for the turn, plus session total. |

`src/types.ts` is a **copy** of the types in `packages/retrieval/src/types.ts` (so this folder can be copied alone). If the host has a contracts package, replace the import with it; if the retrieval types change, update the copy.

## Wiring the client

```ts
import type { RetrievalClient } from './retrieval-ui/src/index.ts'

const client: RetrievalClient = {
  search: (opts) => rpc.retrieval.search(opts),   // -> SearchHit[]
  def: (symbol) => rpc.retrieval.def(symbol),     // -> DefHit[]
  recall: (opts) => rpc.retrieval.recall(opts),   // optional; enables the "#" mode
}
```

Keep `client` referentially stable (module constant or `useMemo`); the palette recreates its controller when it changes. The server side calls `search`/`def`/`recall` from `packages/retrieval` (those functions are synchronous; wrap them in the RPC handler). `onOpenLocation(path, line)` receives the workspace-relative path exactly as the index stored it; resolve it against the workspace root in the host.

## Where to render

- **Palette**: once, at the app shell. Registers a global `keydown` for Cmd/Ctrl+`hotkey` (default `k`; `hotkey={null}` disables; `open`/`onOpenChange` make it controlled). It renders a default trigger button; pass `hideTrigger` if the host has its own (then focus returns to whatever was focused before opening). Check that Lexical's own Ctrl+K handling (if any) does not conflict.
- **Chips**: under each assistant message inside the LegendList item. Compute citations once per finished turn (`extractCitationsFromTurn(turnMessages)`) and store them with the message, not per render. Pass `maxVisible` to tune the collapse.
- **Meter**: chat header or footer. Collect SDK `result` messages per session, call `accumulateUsage(results)` and pass `turn={summary.turns.at(-1)}` and `session={summary.session}`.

Usage semantics (verified against the installed `claude-agent-sdk` 0.3.302 `sdk.d.ts` doc comments): `usage` is main-loop only and per-turn in streaming-input sessions; `total_cost_usd` is cumulative, so per-turn cost is the difference between consecutive results. Subagent/auxiliary calls are not in `usage`. If the host runs one `query()` per user turn instead of streaming input, cumulative semantics may differ: verify on real output.

## Tailwind 4 setup in the host

Add to the host's main CSS so utilities used here are generated (adjust the relative path):

```css
@source "../path/to/retrieval-ui/src";
```

The components use `dark:` variants. Tailwind 4's default `dark:` follows `prefers-color-scheme`; if the host toggles a `.dark` class, it already needs `@custom-variant dark (&:where(.dark, .dark *));` (the demo does this in `demo/styles.css`). The components use plain slate/blue/yellow/red palette colors, not host tokens; override via the `className` prop on the wrapper (palette trigger, chips list, meter). The palette popup itself has no `className` hook yet. `sr-only`, `motion-safe:` and `data-[starting-style]` are standard Tailwind 4 utilities/variants.

## Base UI APIs used (checked in installed 1.9.0 `.d.ts`)

`Dialog.Root` (`open`, `onOpenChange`), `Dialog.Trigger`, `Dialog.Portal`, `Dialog.Backdrop`, `Dialog.Popup` (`initialFocus` ref), `Dialog.Title`, `Dialog.Description`; `Tooltip.Provider` (`delay`), `Tooltip.Root`, `Tooltip.Trigger` (renders a `button`), `Tooltip.Portal`, `Tooltip.Positioner` (`sideOffset`), `Tooltip.Popup`. The listbox is hand-rolled (not Base UI Combobox) because results come from an async server and Combobox's client-side filtering model adds nothing here.

## Accessibility notes

- Palette: `role="combobox"` input with `aria-controls`, `aria-expanded`, `aria-activedescendant`; `role="listbox"` / `role="option"` with `aria-selected`. Arrow keys wrap, Enter opens, Esc closes (Base UI), IME composition ignored. Focus moves to the input on open and returns to the trigger (or previously focused element) on close (Base UI default).
- Result count / loading / failure announced through a polite live region; errors also render as `role="alert"`.
- Chips are `<button>`s in a labeled list; aria-label carries the full path; tooltip opens on hover and keyboard focus.
- Meter: visual text is `aria-hidden`; a screen-reader sentence carries full numbers.
- Focus rings on all interactive elements, min 44 px height on triggers, chips, options. Colors use slate-900/100 text on white/slate-900 and slate-600/400 for secondary text (intended >= 4.5:1; computed from Tailwind palette values, not measured in a browser). Transitions use `motion-safe:`.
- Recall results are shown but not openable unless `onOpenRecall` is provided.

## Run

```sh
npm i --ignore-scripts
npm test            # node:test on src/logic (Node >= 24, type stripping)
npm run typecheck
npm run demo        # Vite dev server with fake client; try "websocket", "@connect", "#sqlite", "boom"
npm run build:demo
```

## Not tested

- **No component/DOM tests and no browser run.** The components compile (tsc), bundle, and the dev server serves them, but keyboard flow, focus return, tooltip behavior, screen-reader output, and the 360 px / 200% zoom layout have not been exercised in a browser.
- Contrast ratios and dark mode are not measured.
- Inside the host: LegendList item measurement with expanding chips, Lexical Ctrl+K conflicts, Electron focus behavior, real RPC latency.
- `extractCitationsFromTurn` is tested with hand-written SDK-shaped objects, not captured live SDK output. The citation parser assumes the text formats in `agent-tools.ts`; a path containing `:N-M` followed by two spaces would parse ambiguously.
- Windows paths and `recall` flow against a real index.
