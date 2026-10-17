import { createHash } from 'node:crypto'
import type { DatabaseSync } from 'node:sqlite'
import type { Embedder } from './types.ts'

export const EMBED_BATCH_SIZE = 32
// bge-small takes 512 tokens; code runs ~3 chars/token.
export const MAX_EMBED_CHARS = 1500

// Connections that have vec0 loaded and a matching chunks_vec table. Vectors are per-connection opt-in.
const enabledConnections = new WeakMap<DatabaseSync, { embedderId: string; dims: number }>()

export interface EnableVectorsOptions {
  // Packaged Electron passes the app.asar.unpacked path to the sqlite-vec loadable.
  extensionPath?: string
  dims: number
  embedderId: string
}

export async function enableVectors(db: DatabaseSync, opts: EnableVectorsOptions): Promise<void> {
  if (!Number.isInteger(opts.dims) || opts.dims < 1) throw new Error(`enableVectors: invalid dims ${opts.dims}`)
  let path = opts.extensionPath
  if (path === undefined) path = (await import('sqlite-vec')).getLoadablePath()
  // Loading the same extension twice on one connection is a no-op.
  db.loadExtension(path)

  db.exec(`create table if not exists vec_meta (key text primary key, value text not null)`)
  // Hash of the embedded input per chunk: chunk ids can be reused after deletes, so id alone cannot prove freshness.
  db.exec(`create table if not exists vec_chunks (chunk_id integer primary key, hash text not null)`)
  const stored = new Map(
    (db.prepare('select key, value from vec_meta').all() as unknown as { key: string; value: string }[]).map((r) => [r.key, r.value]),
  )
  const changed = stored.get('embedder_id') !== opts.embedderId || stored.get('dims') !== String(opts.dims)

  db.exec('BEGIN')
  try {
    if (changed) {
      db.exec('drop table if exists chunks_vec')
      db.exec('delete from vec_chunks')
    }
    db.exec(`create virtual table if not exists chunks_vec using vec0(chunk_id integer primary key, embedding float[${opts.dims}])`)
    const put = db.prepare('insert or replace into vec_meta(key, value) values (?, ?)')
    put.run('embedder_id', opts.embedderId)
    put.run('dims', String(opts.dims))
    db.exec('COMMIT')
  } catch (error) {
    db.exec('ROLLBACK')
    throw error
  }
  enabledConnections.set(db, { embedderId: opts.embedderId, dims: opts.dims })
}

export function vectorsEnabledFor(db: DatabaseSync, embedder: Embedder): boolean {
  const state = enabledConnections.get(db)
  return state !== undefined && state.embedderId === embedder.id && state.dims === embedder.dims
}

export function toBlob(vector: Float32Array): Uint8Array {
  return new Uint8Array(vector.buffer, vector.byteOffset, vector.byteLength)
}

interface ChunkInputRow {
  id: number
  path: string
  symbol: string | null
  text: string
}

export function embedInput(row: Omit<ChunkInputRow, 'id'>): string {
  return [row.symbol ?? '', row.path, row.text].join('\n').slice(0, MAX_EMBED_CHARS)
}

const hashOf = (input: string) => createHash('sha1').update(input).digest('hex')

export interface EmbedProgress {
  embedded: number
  total: number
}

export interface EmbedPendingOptions {
  batchSize?: number
  signal?: AbortSignal
  onProgress?: (progress: EmbedProgress) => void
}

export interface EmbedPendingResult {
  embedded: number
  removed: number
  aborted: boolean
}

// Separate background pass: indexing stays FTS-only. Safe to abort and re-run; each batch is one transaction.
export async function embedPending(db: DatabaseSync, embedder: Embedder, opts: EmbedPendingOptions = {}): Promise<EmbedPendingResult> {
  if (!vectorsEnabledFor(db, embedder)) throw new Error('embedPending: call enableVectors with this embedder first')
  const batchSize = Math.max(1, opts.batchSize ?? EMBED_BATCH_SIZE)

  const stored = new Map(
    (db.prepare('select chunk_id, hash from vec_chunks').all() as unknown as { chunk_id: number; hash: string }[]).map((r) => [r.chunk_id, r.hash]),
  )
  const pending: number[] = []
  const live = new Set<number>()
  for (const row of db.prepare('select id, path, symbol, text from chunks order by id').iterate() as Iterable<ChunkInputRow>) {
    live.add(row.id)
    if (stored.get(row.id) !== hashOf(embedInput(row))) pending.push(row.id)
  }

  const orphans = [...stored.keys()].filter((id) => !live.has(id))
  if (orphans.length > 0) {
    db.exec('BEGIN')
    try {
      const dropVec = db.prepare('delete from chunks_vec where chunk_id = ?')
      const dropMeta = db.prepare('delete from vec_chunks where chunk_id = ?')
      for (const id of orphans) {
        dropVec.run(BigInt(id))
        dropMeta.run(id)
      }
      db.exec('COMMIT')
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    }
  }

  const selectChunk = db.prepare('select id, path, symbol, text from chunks where id = ?')
  const deleteVec = db.prepare('delete from chunks_vec where chunk_id = ?')
  const insertVec = db.prepare('insert into chunks_vec(chunk_id, embedding) values (?, ?)')
  const upsertMeta = db.prepare('insert or replace into vec_chunks(chunk_id, hash) values (?, ?)')

  let embedded = 0
  for (let start = 0; start < pending.length; start += batchSize) {
    if (opts.signal?.aborted) return { embedded, removed: orphans.length, aborted: true }
    const rows = pending
      .slice(start, start + batchSize)
      .map((id) => selectChunk.get(id) as unknown as ChunkInputRow | undefined)
      .filter((row): row is ChunkInputRow => row !== undefined)
    if (rows.length === 0) continue
    const inputs = rows.map(embedInput)
    const vectors = await embedder.embed(inputs, 'document')
    if (vectors.length !== rows.length || vectors.some((v) => v.length !== embedder.dims)) {
      throw new Error(`embedPending: embedder ${embedder.id} returned wrong vector count or dims (expected ${embedder.dims})`)
    }
    db.exec('BEGIN')
    try {
      rows.forEach((row, i) => {
        deleteVec.run(BigInt(row.id))
        insertVec.run(BigInt(row.id), toBlob(vectors[i]!))
        upsertMeta.run(row.id, hashOf(inputs[i]!))
      })
      db.exec('COMMIT')
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    }
    embedded += rows.length
    opts.onProgress?.({ embedded, total: pending.length })
  }
  return { embedded, removed: orphans.length, aborted: false }
}
