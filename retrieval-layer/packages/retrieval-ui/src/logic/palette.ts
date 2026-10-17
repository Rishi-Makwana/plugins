import type { RetrievalClient } from '../types.ts'

export type PaletteMode = 'search' | 'def' | 'recall'

export type PaletteItem =
  | { key: string; kind: 'location'; mode: 'search' | 'def'; path: string; line: number; endLine?: number; symbol?: string; snippet: string }
  | { key: string; kind: 'recall'; sessionId: string; turnIndex: number; role: string; snippet: string }

export interface PaletteState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  mode: PaletteMode
  term: string
  items: PaletteItem[]
  error: string | null
}

export const DEBOUNCE_MS = 120
export const PALETTE_K = 10

export const IDLE_STATE: PaletteState = { status: 'idle', mode: 'search', term: '', items: [], error: null }

// '@x' = definition lookup, '#x' = chat recall (only when the client supports it); otherwise plain search.
export function parseQuery(raw: string, canRecall: boolean): { mode: PaletteMode; term: string } {
  const text = raw.trim()
  if (text.startsWith('@')) return { mode: 'def', term: text.slice(1).trim() }
  if (canRecall && text.startsWith('#')) return { mode: 'recall', term: text.slice(1).trim() }
  return { mode: 'search', term: text }
}

// Wraps in both directions; -1 means "nothing active yet" so ArrowDown lands on 0 and ArrowUp on the last.
export function moveIndex(current: number, delta: 1 | -1, length: number): number {
  if (length <= 0) return -1
  if (current < 0) return delta === 1 ? 0 : length - 1
  return (current + delta + length) % length
}

async function fetchItems(client: RetrievalClient, mode: PaletteMode, term: string): Promise<PaletteItem[]> {
  if (mode === 'def') {
    const hits = await client.def(term)
    return hits.map((h, i) => ({ key: `def:${i}:${h.path}:${h.line}`, kind: 'location', mode: 'def', path: h.path, line: h.line, snippet: h.signature }))
  }
  if (mode === 'recall') {
    if (!client.recall) return []
    const hits = await client.recall({ query: term, k: PALETTE_K })
    return hits.map((h) => ({ key: `recall:${h.sessionId}:${h.turnIndex}`, kind: 'recall', sessionId: h.sessionId, turnIndex: h.turnIndex, role: h.role, snippet: h.snippet }))
  }
  const hits = await client.search({ query: term, k: PALETTE_K })
  return hits.map((h, i) => ({
    key: `search:${i}:${h.path}:${h.startLine}`,
    kind: 'location',
    mode: 'search',
    path: h.path,
    line: h.startLine,
    endLine: h.endLine,
    ...(h.symbol ? { symbol: h.symbol } : {}),
    snippet: h.snippet,
  }))
}

export interface PaletteController {
  setInput(raw: string): void
  getState(): PaletteState
  dispose(): void
}

export function createPaletteController(
  client: RetrievalClient,
  onChange: (state: PaletteState) => void,
  debounceMs: number = DEBOUNCE_MS,
): PaletteController {
  let state = IDLE_STATE
  let timer: ReturnType<typeof setTimeout> | undefined
  // Every input bumps the generation; a response applies only if it is still the latest one.
  let generation = 0
  let disposed = false

  const publish = (next: PaletteState) => {
    state = next
    if (!disposed) onChange(state)
  }

  return {
    getState: () => state,
    setInput(raw) {
      const mine = ++generation
      clearTimeout(timer)
      const { mode, term } = parseQuery(raw, typeof client.recall === 'function')
      if (term === '') return publish({ ...IDLE_STATE, mode })
      publish({ status: 'loading', mode, term, items: state.term === term && state.mode === mode ? state.items : [], error: null })
      timer = setTimeout(() => {
        fetchItems(client, mode, term).then(
          (items) => {
            if (mine === generation) publish({ status: 'ready', mode, term, items, error: null })
          },
          (error: unknown) => {
            if (mine === generation) publish({ status: 'error', mode, term, items: [], error: error instanceof Error ? error.message : String(error) })
          },
        )
      }, debounceMs)
    },
    dispose() {
      disposed = true
      generation++
      clearTimeout(timer)
    },
  }
}

// Split text into alternating plain/match segments for safe highlighting (rendered as React text nodes).
export function highlightSegments(text: string, term: string): { text: string; match: boolean }[] {
  const words = [...new Set(term.split(/\s+/).filter((w) => w.length > 0))].sort((a, b) => b.length - a.length)
  if (words.length === 0 || text === '') return [{ text, match: false }]
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'giu')
  return text
    .split(pattern)
    .map((part, i) => ({ text: part, match: i % 2 === 1 }))
    .filter((s) => s.text !== '')
}
