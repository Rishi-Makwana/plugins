# Chat UI rendering performance — task brief

**For an AI coding agent working in the host repo.** Separate from the retrieval layer. Nothing here has been measured on the host app; these are the usual hotspots for its stack (React 19, LegendList, Lexical, xterm.js, WebSocket RPC streaming). Rule: **profile first, fix only what the profile shows.**

## Step 1 — Measure (do this before any change)

1. In a dev build, open DevTools → Performance. Record while the agent streams a long response (≥ 2,000 lines of markdown with code blocks), plus one turn with a long-running tool in the terminal view.
2. Also record with React DevTools Profiler: which components re-render per streamed token, and their render time.
3. Write down: longest task (ms), dropped frames, the top 3 functions by self time, re-renders per token.
4. Target: every frame < 16 ms while streaming.

Add a cheap regression check if feasible: a script that replays a recorded token stream into the chat view and reports average and p95 frame time.

## Step 2 — Fix only what Step 1 shows

Ordered by how often each is the cause in this kind of app:

1. **Whole message re-parsed per token.** Split the streaming message into markdown blocks. Completed blocks render as memoized components keyed by block index and never re-parse. Only the trailing incomplete block re-renders.
2. **One render per token.** Coalesce tokens before they hit React state: buffer on the server and send frames every ~30 ms, and/or buffer in the renderer and flush once per `requestAnimationFrame`.
3. **xterm.js instances kept alive for finished tools.** Keep one live terminal per running tool. When a tool finishes, render its output as static text and dispose the xterm instance.
4. **Lexical used to render history.** Keep Lexical for the composer only. Render past messages with a plain markdown renderer.
5. **LegendList config.** `estimatedItemSize` near the real median message height, `recycleItems` on, stable keys, memoized items. The streaming item grows via the list's measurement path, not by remounting.
6. **React Compiler.** If not enabled, check whether enabling it in the Vite config removes the needless re-renders found in Step 1.

## Step 3 — Prove it

Re-record the same scenario. Report before/after for longest task, p95 frame time and re-renders per token. Revert any change that does not move a measured number.
