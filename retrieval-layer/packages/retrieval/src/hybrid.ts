import type { DatabaseSync } from 'node:sqlite'
import { buildMatchQuery, clampK, DEFAULT_SEARCH_KINDS, dropContainers, escapeLike, rankedRows, recall, search } from './search.ts'
import type { Row } from './search.ts'
import type { Embedder, RecallHit, RecallOptions, SearchHit, SearchOptions } from './types.ts'
import { SNIPPET_MAX_LINES } from './types.ts'
import { toBlob, vectorsEnabledFor } from './vectors.ts'

export const RRF_K = 60
export const HYBRID_CANDIDATES = 50
// KNN cannot filter by kind/path inside vec0, so over-fetch and filter afterwards.
const KNN_OVERFETCH = 4

export type FallbackReporter = (reason: string) => void
const warn: FallbackReporter = (reason) => console.warn(`retrieval: hybrid search unavailable, using FTS only (${reason})`)

// Each list is best-first. Score = sum over lists of 1 / (RRF_K + rank), rank 1-based.
export function reciprocalRankFusion(lists: number[][]): { id: number; score: number }[] {
  const scores = new Map<number, number>()
  for (const list of lists) {
    list.forEach((id, index) => scores.set(id, (scores.get(id) ?? 0) + 1 / (RRF_K + index + 1)))
  }
  return [...scores].map(([id, score]) => ({ id, score })).sort((a, b) => b.score - a.score || a.id - b.id)
}

// Filters are SQL conditions on alias `c`.
async function fuse(
  db: DatabaseSync,
  embedder: Embedder,
  query: string,
  filters: string[],
  params: (string | number)[],
): Promise<{ rows: Row[]; scores: Map<number, number> }> {
  const match = buildMatchQuery(query)
  const ftsRows = match === null ? [] : rankedRows(db, ['chunks_fts match ?', ...filters], [match, ...params], HYBRID_CANDIDATES)

  const [queryVector] = await embedder.embed([query], 'query')
  if (!queryVector || queryVector.length !== embedder.dims) throw new Error('embedder returned no usable query vector')
  const neighbours = db
    .prepare('select chunk_id from chunks_vec where embedding match ? and k = ? order by distance')
    .all(toBlob(queryVector), HYBRID_CANDIDATES * KNN_OVERFETCH) as unknown as { chunk_id: number }[]
  const knnIds = neighbours.map((n) => n.chunk_id)
  const byId = new Map(ftsRows.map((row) => [row.id, row]))
  const vecRows = new Map<number, Row>()
  if (knnIds.length > 0) {
    const found = db
      .prepare(
        `select c.id, c.path, c.start_line, c.end_line, c.symbol, c.text, c.session_id, c.role, c.created_at, 0 as rank
         from chunks c where c.id in (${knnIds.map(() => '?').join(',')}) and ${filters.length > 0 ? filters.join(' and ') : '1'}`,
      )
      .all(...knnIds, ...params) as unknown as Row[]
    for (const row of found) vecRows.set(row.id, row)
  }
  const knnFiltered = knnIds.filter((id) => vecRows.has(id)).slice(0, HYBRID_CANDIDATES)
  for (const id of knnFiltered) if (!byId.has(id)) byId.set(id, vecRows.get(id)!)

  const fused = reciprocalRankFusion([ftsRows.map((r) => r.id), knnFiltered])
  return { rows: fused.map((f) => byId.get(f.id)!), scores: new Map(fused.map((f) => [f.id, f.score])) }
}

export async function hybridSearch(
  db: DatabaseSync,
  embedder: Embedder,
  opts: SearchOptions,
  onFallback: FallbackReporter = warn,
): Promise<SearchHit[]> {
  if (!vectorsEnabledFor(db, embedder)) {
    onFallback('vectors not enabled for this embedder')
    return search(db, opts)
  }
  try {
    const k = clampK(opts.k)
    const filters: string[] = []
    const params: (string | number)[] = []
    const kinds = opts.kinds && opts.kinds.length > 0 ? opts.kinds : DEFAULT_SEARCH_KINDS
    filters.push(`c.kind in (${kinds.map(() => '?').join(', ')})`)
    params.push(...kinds)
    if (opts.pathPrefix) {
      filters.push(`c.path like ? escape '\\'`)
      params.push(`${escapeLike(opts.pathPrefix)}%`)
    }
    const { rows, scores } = await fuse(db, embedder, opts.query, filters, params)
    // LIKE is case-insensitive for ASCII; paths are not.
    const candidates = opts.pathPrefix ? rows.filter((row) => row.path.startsWith(opts.pathPrefix!)) : rows
    return dropContainers(candidates)
      .slice(0, k)
      .map((row) => ({
        path: row.path,
        startLine: row.start_line,
        endLine: row.end_line,
        symbol: row.symbol,
        score: scores.get(row.id)!,
        snippet: row.text.split('\n').slice(0, SNIPPET_MAX_LINES).join('\n'),
      }))
  } catch (error) {
    onFallback(error instanceof Error ? error.message : String(error))
    return search(db, opts)
  }
}

export async function hybridRecall(
  db: DatabaseSync,
  embedder: Embedder,
  opts: RecallOptions,
  onFallback: FallbackReporter = warn,
): Promise<RecallHit[]> {
  if (!vectorsEnabledFor(db, embedder)) {
    onFallback('vectors not enabled for this embedder')
    return recall(db, opts)
  }
  try {
    const k = clampK(opts.k)
    const filters = ["c.kind = 'chat'"]
    const params: (string | number)[] = []
    if (opts.sessionId !== undefined) {
      filters.push('c.session_id = ?')
      params.push(opts.sessionId)
    }
    const { rows, scores } = await fuse(db, embedder, opts.query, filters, params)
    return rows.slice(0, k).map((row) => ({
      sessionId: row.session_id!,
      turnIndex: row.start_line,
      role: row.role!,
      createdAt: row.created_at!,
      score: scores.get(row.id)!,
      snippet: row.text.split('\n').slice(0, SNIPPET_MAX_LINES).join('\n'),
    }))
  } catch (error) {
    onFallback(error instanceof Error ? error.message : String(error))
    return recall(db, opts)
  }
}
