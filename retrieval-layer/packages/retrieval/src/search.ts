import { readFileSync, realpathSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import type { DatabaseSync } from 'node:sqlite'
import { identifierTerms } from './store.ts'
import type { ChatRole, DefHit, RecallHit, RecallOptions, SearchHit, SearchOptions } from './types.ts'
import { DEF_MAX_HITS, SEARCH_MAX_K, SNIPPET_MAX_LINES } from './types.ts'

export const DEFAULT_K = 8
export const READ_MAX_LINES = 200
export const SIGNATURE_MAX_CHARS = 200
// bm25 column weights, in chunks_fts column order: text, symbol, path, terms.
export const BM25_WEIGHT_TEXT = 1
export const BM25_WEIGHT_SYMBOL = 4
export const BM25_WEIGHT_PATH = 2
export const BM25_WEIGHT_TERMS = 1
// Over-fetch so overlap removal still leaves k hits.
const CANDIDATE_MULTIPLIER = 5
const MIN_QUERY_TOKEN_CHARS = 2

export function buildMatchQuery(raw: string): string | null {
  const words = raw.match(/[\p{L}\p{N}_]+/gu) ?? []
  const tokens = new Set([...words, ...identifierTerms(raw).split(' ')])
  const quoted = [...tokens]
    .filter((token) => token.length >= MIN_QUERY_TOKEN_CHARS)
    .map((token) => `"${token.replaceAll('"', '""')}"`)
  return quoted.length === 0 ? null : quoted.join(' OR ')
}

function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, (char) => `\\${char}`)
}

interface Row {
  path: string
  start_line: number
  end_line: number
  symbol: string | null
  text: string
  rank: number
  session_id: string | null
  role: ChatRole | null
  created_at: number | null
}

function clampK(requested: number | undefined): number {
  const k = Number.isFinite(requested) ? Math.trunc(requested as number) : DEFAULT_K
  return Math.min(Math.max(k, 1), SEARCH_MAX_K)
}

function rankedRows(db: DatabaseSync, where: string[], params: (string | number)[], k: number): Row[] {
  return db
    .prepare(
      `select c.path, c.start_line, c.end_line, c.symbol, c.text, c.session_id, c.role, c.created_at,
              bm25(chunks_fts, ${BM25_WEIGHT_TEXT}, ${BM25_WEIGHT_SYMBOL}, ${BM25_WEIGHT_PATH}, ${BM25_WEIGHT_TERMS}) as rank
       from chunks_fts join chunks c on c.id = chunks_fts.rowid
       where ${where.join(' and ')}
       order by rank limit ?`,
    )
    .all(...params, k * CANDIDATE_MULTIPLIER) as unknown as Row[]
}

const DEFAULT_SEARCH_KINDS = ['code', 'doc']

export function search(db: DatabaseSync, opts: SearchOptions): SearchHit[] {
  const match = buildMatchQuery(opts.query)
  if (match === null) return []
  const k = clampK(opts.k)

  const where = ['chunks_fts match ?']
  const params: (string | number)[] = [match]
  // Chat is private memory: only reachable by asking for it (or via recall).
  const kinds = opts.kinds && opts.kinds.length > 0 ? opts.kinds : DEFAULT_SEARCH_KINDS
  where.push(`c.kind in (${kinds.map(() => '?').join(', ')})`)
  params.push(...kinds)
  if (opts.pathPrefix) {
    where.push(`c.path like ? escape '\\'`)
    params.push(`${escapeLike(opts.pathPrefix)}%`)
  }

  const rows = rankedRows(db, where, params, k)

  // LIKE is case-insensitive for ASCII; paths are not.
  const candidates = opts.pathPrefix ? rows.filter((row) => row.path.startsWith(opts.pathPrefix!)) : rows
  return dropContainers(candidates)
    .slice(0, k)
    .map((row) => ({
      path: row.path,
      startLine: row.start_line,
      endLine: row.end_line,
      symbol: row.symbol,
      score: -row.rank,
      snippet: row.text.split('\n').slice(0, SNIPPET_MAX_LINES).join('\n'),
    }))
}

// Rows arrive best-first. A hit whose range strictly contains another hit in the same file is dropped.
function dropContainers(rows: Row[]): Row[] {
  return rows.filter((row, index) =>
    !rows.some(
      (other, otherIndex) =>
        otherIndex !== index &&
        other.path === row.path &&
        other.start_line >= row.start_line &&
        other.end_line <= row.end_line &&
        (other.start_line !== row.start_line || other.end_line !== row.end_line || otherIndex < index),
    ),
  )
}

export function recall(db: DatabaseSync, opts: RecallOptions): RecallHit[] {
  const match = buildMatchQuery(opts.query)
  if (match === null) return []
  const k = clampK(opts.k)
  const where = ['chunks_fts match ?', "c.kind = 'chat'"]
  const params: (string | number)[] = [match]
  if (opts.sessionId !== undefined) {
    where.push('c.session_id = ?')
    params.push(opts.sessionId)
  }
  return rankedRows(db, where, params, k)
    .slice(0, k)
    .map((row) => ({
      sessionId: row.session_id!,
      turnIndex: row.start_line,
      role: row.role!,
      createdAt: row.created_at!,
      score: -row.rank,
      snippet: row.text.split('\n').slice(0, SNIPPET_MAX_LINES).join('\n'),
    }))
}

export function def(db: DatabaseSync, symbol: string): DefHit[] {
  const name = symbol.trim()
  if (name === '') return []
  const stages: [string, string][] = [
    ['symbol = ?', name],
    [`symbol like ? escape '\\'`, `%.${escapeLike(name)}`],
    [`symbol like ? escape '\\'`, `${escapeLike(name)}%`],
  ]
  const hits: DefHit[] = []
  const seen = new Set<string>()
  for (const [condition, param] of stages) {
    const rows = db
      .prepare(
        `select path, start_line, text from chunks
         where symbol_kind is not null and ${condition}
         order by path, start_line limit ?`,
      )
      .all(param, DEF_MAX_HITS) as unknown as { path: string; start_line: number; text: string }[]
    for (const row of rows) {
      const key = `${row.path}:${row.start_line}`
      if (seen.has(key)) continue
      seen.add(key)
      hits.push({ path: row.path, line: row.start_line, signature: firstSignatureLine(row.text) })
      if (hits.length >= DEF_MAX_HITS) return hits
    }
  }
  return hits
}

function firstSignatureLine(text: string): string {
  const line = text.split('\n').find((candidate) => candidate.trim() !== '') ?? ''
  return line.trim().slice(0, SIGNATURE_MAX_CHARS)
}

// Trust boundary: path comes from the agent. Rejects absolute paths, `..` escapes and symlink escapes.
export function readRange(root: string, path: string, startLine: number, endLine: number): string {
  if (!Number.isInteger(startLine) || !Number.isInteger(endLine) || startLine < 1 || endLine < startLine) {
    throw new Error(`readRange: invalid line range ${startLine}-${endLine}`)
  }
  if (isAbsolute(path) || path.includes('\0')) throw new Error(`readRange: path must be relative to the workspace: ${path}`)
  const realRoot = realpathSync(root)
  const target = resolve(realRoot, path)
  if (!isInside(realRoot, target)) throw new Error(`readRange: path escapes the workspace: ${path}`)
  const realTarget = realpathSync(target)
  if (!isInside(realRoot, realTarget)) throw new Error(`readRange: path escapes the workspace: ${path}`)

  const lines = readFileSync(realTarget, 'utf8').split(/\r?\n/)
  const last = Math.min(endLine, startLine + READ_MAX_LINES - 1)
  return lines.slice(startLine - 1, last).join('\n')
}

function isInside(root: string, target: string): boolean {
  const rel = relative(root, target)
  return rel !== '' && rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel)
}
