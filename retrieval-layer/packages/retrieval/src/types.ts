// Shared contract between chunker, indexer and search. Change only with both sides updated.

export type SymbolKind = 'fn' | 'class' | 'method' | 'type'

export type ChunkKind = 'code' | 'doc' | 'chat'

// Lines are 1-based and inclusive.
export interface Chunk {
  symbol: string | null
  symbolKind: SymbolKind | null
  startLine: number
  endLine: number
  text: string
}

export interface Chunker {
  // Returns null when the file type is not supported at all (indexer then skips it).
  chunkFile(path: string, content: string): Promise<{ kind: ChunkKind; chunks: Chunk[] } | null>
}

export interface SearchOptions {
  query: string
  k?: number
  kinds?: ChunkKind[]
  pathPrefix?: string
}

export interface Embedder {
  id: string
  dims: number
  // `kind` exists because many models prefix queries and documents differently.
  embed(texts: string[], kind: 'query' | 'document'): Promise<Float32Array[]>
}

export type ChatRole = 'user' | 'assistant'

export interface ChatTurn {
  sessionId: string
  turnIndex: number
  role: ChatRole
  text: string
  // Epoch milliseconds.
  createdAt: number
}

export interface RecallOptions {
  query: string
  k?: number
  sessionId?: string
}

export interface RecallHit {
  sessionId: string
  turnIndex: number
  role: ChatRole
  createdAt: number
  score: number
  snippet: string
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

export interface IndexStats {
  filesSeen: number
  filesIndexed: number
  filesUnchanged: number
  filesRemoved: number
  filesSkipped: number
  chunks: number
  ms: number
}

export const SEARCH_MAX_K = 10
export const SNIPPET_MAX_LINES = 20
export const DEF_MAX_HITS = 5
export const MAX_FILE_BYTES = 1_000_000
export const MAX_CHUNK_LINES = 150
export const WINDOW_LINES = 60
export const WINDOW_OVERLAP = 10
