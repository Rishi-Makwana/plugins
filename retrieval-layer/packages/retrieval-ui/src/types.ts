// Mirrors packages/retrieval/src/types.ts (copied so this package is self-contained). Keep in sync by hand.

export type ChunkKind = 'code' | 'doc' | 'chat'
export type ChatRole = 'user' | 'assistant'

export interface SearchOptions {
  query: string
  k?: number
  kinds?: ChunkKind[]
  pathPrefix?: string
}

export interface RecallOptions {
  query: string
  k?: number
  sessionId?: string
}

export interface SearchHit {
  path: string
  startLine: number
  endLine: number
  symbol: string | null
  score: number
  snippet: string
}

export interface DefHit {
  path: string
  line: number
  signature: string
}

export interface RecallHit {
  sessionId: string
  turnIndex: number
  role: ChatRole
  createdAt: number
  score: number
  snippet: string
}

// The only seam to the host: implement these over the host's WebSocket RPC.
export interface RetrievalClient {
  search(opts: SearchOptions): Promise<SearchHit[]>
  def(symbol: string): Promise<DefHit[]>
  recall?(opts: RecallOptions): Promise<RecallHit[]>
}

export type OpenLocation = (path: string, line: number) => void

export interface Citation {
  path: string
  startLine: number
  endLine?: number
  symbol?: string
}
