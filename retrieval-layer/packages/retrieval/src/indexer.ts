import { createHash } from 'node:crypto'
import { existsSync, lstatSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { DatabaseSync } from 'node:sqlite'
import ignore from 'ignore'
import type { Chunker, IndexStats } from './types.ts'
import { MAX_FILE_BYTES } from './types.ts'
import { identifierTerms } from './store.ts'

export type IndexFileResult = 'indexed' | 'unchanged' | 'skipped' | 'removed'

const ALWAYS_SKIPPED_DIRS = new Set(['.git', 'node_modules'])
const BINARY_SNIFF_BYTES = 8192

interface IgnoreScope {
  // Directory the .gitignore lives in, relative to root ('' for root), no trailing slash.
  dir: string
  matcher: ReturnType<typeof ignore>
}

function inTransaction(db: DatabaseSync, work: () => void): void {
  db.exec('BEGIN')
  try {
    work()
    db.exec('COMMIT')
  } catch (error) {
    db.exec('ROLLBACK')
    throw error
  }
}

function isIgnored(scopes: IgnoreScope[], relPath: string, isDir: boolean): boolean {
  let ignored = false
  // Deeper .gitignore files take precedence over shallower ones, as in git.
  for (const scope of scopes) {
    const local = scope.dir === '' ? relPath : relPath.slice(scope.dir.length + 1)
    const result = scope.matcher.test(isDir ? `${local}/` : local)
    if (result.ignored) ignored = true
    else if (result.unignored) ignored = false
  }
  return ignored
}

function walk(root: string, dirRel: string, scopes: IgnoreScope[], out: string[]): void {
  const absDir = dirRel === '' ? root : join(root, dirRel)
  const gitignorePath = join(absDir, '.gitignore')
  let active = scopes
  if (existsSync(gitignorePath)) {
    active = [...scopes, { dir: dirRel, matcher: ignore().add(readFileSync(gitignorePath, 'utf8')) }]
  }
  const entries = readdirSync(absDir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))
  for (const entry of entries) {
    // Symlinks are skipped entirely: no cycles, and nothing can point outside root.
    if (entry.isSymbolicLink()) continue
    const rel = dirRel === '' ? entry.name : `${dirRel}/${entry.name}`
    if (entry.isDirectory()) {
      if (ALWAYS_SKIPPED_DIRS.has(entry.name) || isIgnored(active, rel, true)) continue
      walk(root, rel, active, out)
    } else if (entry.isFile() && !isIgnored(active, rel, false)) {
      out.push(rel)
    }
  }
}

export function removeFile(db: DatabaseSync, relPath: string): void {
  inTransaction(db, () => {
    db.prepare('delete from chunks where path = ?').run(relPath)
    db.prepare('delete from indexed_files where path = ?').run(relPath)
  })
}

export async function indexFile(
  db: DatabaseSync,
  root: string,
  relPath: string,
  chunker: Chunker,
): Promise<IndexFileResult> {
  const abs = join(root, relPath)
  let stat
  try {
    stat = lstatSync(abs)
  } catch {
    removeFile(db, relPath)
    return 'removed'
  }
  if (!stat.isFile() || stat.size > MAX_FILE_BYTES) return skip(db, relPath)

  const buffer = readFileSync(abs)
  if (buffer.subarray(0, BINARY_SNIFF_BYTES).includes(0)) return skip(db, relPath)

  const hash = createHash('sha256').update(buffer).digest('hex')
  const existing = db.prepare('select hash from indexed_files where path = ?').get(relPath) as
    | { hash: string }
    | undefined
  if (existing?.hash === hash) return 'unchanged'

  const chunked = await chunker.chunkFile(relPath, buffer.toString('utf8'))
  if (chunked === null) return skip(db, relPath)

  inTransaction(db, () => {
    db.prepare('delete from chunks where path = ?').run(relPath)
    const insert = db.prepare(
      `insert into chunks (path, kind, symbol, symbol_kind, start_line, end_line, text, terms)
       values (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    for (const chunk of chunked.chunks) {
      insert.run(
        relPath,
        chunked.kind,
        chunk.symbol,
        chunk.symbolKind,
        chunk.startLine,
        chunk.endLine,
        chunk.text,
        identifierTerms(`${chunk.symbol ?? ''} ${chunk.text}`),
      )
    }
    db.prepare(
      `insert into indexed_files (path, hash, mtime, indexed_at) values (?, ?, ?, ?)
       on conflict(path) do update set hash = excluded.hash, mtime = excluded.mtime, indexed_at = excluded.indexed_at`,
    ).run(relPath, hash, Math.floor(stat.mtimeMs), Date.now())
  })
  return 'indexed'
}

// A file that became too big, binary or unsupported must not keep stale chunks.
function skip(db: DatabaseSync, relPath: string): IndexFileResult {
  const wasIndexed = db.prepare('select 1 from indexed_files where path = ?').get(relPath)
  if (wasIndexed) removeFile(db, relPath)
  return 'skipped'
}

export async function indexWorkspace(db: DatabaseSync, root: string, chunker: Chunker): Promise<IndexStats> {
  const started = performance.now()
  const files: string[] = []
  walk(root, '', [], files)

  const stats: IndexStats = {
    filesSeen: files.length,
    filesIndexed: 0,
    filesUnchanged: 0,
    filesRemoved: 0,
    filesSkipped: 0,
    chunks: 0,
    ms: 0,
  }
  const present = new Set<string>()
  for (const rel of files) {
    const result = await indexFile(db, root, rel, chunker)
    if (result === 'indexed') stats.filesIndexed++
    else if (result === 'unchanged') stats.filesUnchanged++
    else stats.filesSkipped++
    if (result === 'indexed' || result === 'unchanged') present.add(rel)
  }

  const known = db.prepare('select path from indexed_files').all() as { path: string }[]
  for (const { path } of known) {
    if (present.has(path)) continue
    removeFile(db, path)
    stats.filesRemoved++
  }

  stats.chunks = (db.prepare('select count(*) as n from chunks').get() as { n: number }).n
  stats.ms = performance.now() - started
  return stats
}
