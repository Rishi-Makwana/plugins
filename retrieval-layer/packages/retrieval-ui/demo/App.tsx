import { useEffect, useMemo, useState } from 'react'
import { CitationChips, CommandPalette, TokenMeter, accumulateUsage, extractCitationsFromTurn } from '../src/index.ts'
import { fakeClient } from './fakeClient.ts'

// Shaped like Agent SDK messages (assistant tool_use, user tool_result, result).
const turnMessages = [
  { type: 'assistant', message: { content: [{ type: 'tool_use', id: 't1', name: 'mcp__retrieval__search', input: { query: 'websocket auth token' } }, { type: 'tool_use', id: 't2', name: 'mcp__retrieval__def', input: {} }] } },
  {
    type: 'user',
    message: {
      content: [
        { type: 'tool_result', tool_use_id: 't1', content: [{ type: 'text', text: 'src/server/connectHandler.ts:10-42  connectHandler (score 4.2)\nbody\n\nsrc/server/auth.ts:1-28  readAuthToken (score 3.1)\nbody\n\ndocs/websocket auth: notes.md:4-19  - (score 1.8)\nbody\n\nsrc/日本語/ソケット.ts:3-9  openSocket (score 1.2)\nbody\n\nsrc/client/rpc.ts:55-90  RpcClient (score 1.1)\nbody' }] },
        { type: 'tool_result', tool_use_id: 't2', content: 'src/server/connectHandler.ts:10  export async function connectHandler(socket: WebSocket) {' },
      ],
    },
  },
]
const results = [
  { type: 'result', usage: { input_tokens: 3200, output_tokens: 410, cache_read_input_tokens: 18000, cache_creation_input_tokens: 0 }, total_cost_usd: 0.021 },
  { type: 'result', usage: { input_tokens: 2100, output_tokens: 1250, cache_read_input_tokens: 24500, cache_creation_input_tokens: 900 }, total_cost_usd: 0.062 },
]

export function App() {
  const [dark, setDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches)
  const [opened, setOpened] = useState('nothing opened yet')
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])
  const citations = useMemo(() => extractCitationsFromTurn(turnMessages), [])
  const usage = useMemo(() => accumulateUsage(results), [])
  const open = (path: string, line: number) => setOpened(`${path}:${line}`)

  return (
    <div className="min-h-screen bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-300 p-3 dark:border-slate-700">
        <h1 className="text-base font-semibold">retrieval-ui playground</h1>
        <div className="flex flex-wrap items-center gap-2">
          <TokenMeter turn={usage.turns.at(-1)} session={usage.session} />
          <CommandPalette client={fakeClient} onOpenLocation={open} onOpenRecall={(h) => setOpened(`chat ${h.sessionId} turn ${h.turnIndex}`)} />
          <button
            type="button"
            aria-pressed={dark}
            onClick={() => setDark((d) => !d)}
            className="min-h-11 rounded-lg border border-slate-300 px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700 dark:border-slate-600 dark:focus-visible:outline-blue-400"
          >
            Dark mode
          </button>
        </div>
      </header>
      <main className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
        <p className="self-end rounded-2xl bg-slate-100 px-4 py-2 dark:bg-slate-800">How does websocket auth work?</p>
        <section aria-label="Assistant message" className="flex flex-col gap-3">
          <p>The handshake reads the token from the Authorization header in connectHandler and closes with 4401 if it is missing.</p>
          <CitationChips citations={citations} onOpenLocation={open} />
        </section>
        <p className="text-sm text-slate-700 dark:text-slate-300" role="status">
          Last opened: <code className="font-mono">{opened}</code>. Try Cmd/Ctrl+K, then &quot;websocket&quot;, &quot;@connect&quot;, &quot;#sqlite&quot;, or &quot;boom&quot; (simulated error).
        </p>
      </main>
    </div>
  )
}
