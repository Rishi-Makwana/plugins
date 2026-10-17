// Phase 4 gate: node eval/run.ts --root <repo> --queries <file> [--cache <dir>]
// Queries file: [{ "q": "...", "expect": ["rel/path.ts"], "type": "paraphrase" | "keyword" }]
import { readFileSync, rmSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'
import { createChunker, embedPending, enableVectors, hybridSearch, indexWorkspace, openStore, search } from '../src/index.ts'
import { createTransformersEmbedder } from '../src/embedder-transformers.ts'
import type { SearchHit } from '../src/index.ts'

const TOP_K = 5

interface EvalQuery {
  q: string
  expect: string[]
  type?: 'paraphrase' | 'keyword'
}

const { values } = parseArgs({ options: { root: { type: 'string' }, queries: { type: 'string' }, cache: { type: 'string' } } })
if (!values.root || !values.queries) {
  console.error('usage: node eval/run.ts --root <repo> --queries <file> [--cache <model cache dir>]')
  process.exit(2)
}

const queries = JSON.parse(readFileSync(resolve(values.queries), 'utf8')) as EvalQuery[]
const tempDir = mkdtempSync(join(process.env.TMPDIR ?? tmpdir(), 'retrieval-eval-'))
const db = openStore(join(tempDir, 'eval.db'))

try {
  let started = performance.now()
  const stats = await indexWorkspace(db, resolve(values.root), createChunker())
  console.log(`indexed ${stats.filesIndexed} files, ${stats.chunks} chunks in ${((performance.now() - started) / 1000).toFixed(1)}s`)

  const embedder = createTransformersEmbedder({ cacheDir: values.cache ?? join(process.env.TMPDIR ?? tmpdir(), 'retrieval-models') })
  started = performance.now()
  await embedder.embed(['warm up'], 'document')
  console.log(`model load (first embed): ${((performance.now() - started) / 1000).toFixed(1)}s`)

  await enableVectors(db, { dims: embedder.dims, embedderId: embedder.id })
  started = performance.now()
  const embedded = await embedPending(db, embedder, {
    onProgress: ({ embedded: done, total }) => {
      if (done % 640 === 0 || done === total) process.stderr.write(`\rembedding ${done}/${total}`)
    },
  })
  const seconds = (performance.now() - started) / 1000
  process.stderr.write('\n')
  console.log(`embedded ${embedded.embedded} chunks in ${seconds.toFixed(1)}s (${(embedded.embedded / seconds).toFixed(1)} chunks/s)`)

  const firstRank = (hits: SearchHit[], expect: string[]) => {
    const index = hits.findIndex((hit) => expect.includes(hit.path))
    return index === -1 ? null : index + 1
  }
  const rows: { type: string; q: string; fts: number | null; hybrid: number | null }[] = []
  for (const { q, expect, type } of queries) {
    const fts = search(db, { query: q, k: TOP_K })
    const hybrid = await hybridSearch(db, embedder, { query: q, k: TOP_K }, (reason) => {
      throw new Error(`hybrid fell back: ${reason}`)
    })
    rows.push({ type: type ?? 'all', q, fts: firstRank(fts, expect), hybrid: firstRank(hybrid, expect) })
  }

  const show = (rank: number | null) => (rank === null ? '-' : String(rank))
  console.log('\ntype        FTS  HYB  query')
  for (const row of rows) console.log(`${row.type.padEnd(11)} ${show(row.fts).padStart(3)}  ${show(row.hybrid).padStart(3)}  ${row.q}`)

  const summarize = (label: string, subset: typeof rows) => {
    if (subset.length === 0) return
    const recall = (pick: (r: (typeof rows)[number]) => number | null) => subset.filter((r) => pick(r) !== null).length / subset.length
    const mrr = (pick: (r: (typeof rows)[number]) => number | null) => subset.reduce((sum, r) => sum + (pick(r) === null ? 0 : 1 / pick(r)!), 0) / subset.length
    const fts = (r: (typeof rows)[number]) => r.fts
    const hyb = (r: (typeof rows)[number]) => r.hybrid
    console.log(
      `${label.padEnd(11)} n=${String(subset.length).padStart(2)}  recall@${TOP_K} FTS ${recall(fts).toFixed(2)} HYB ${recall(hyb).toFixed(2)}   MRR FTS ${mrr(fts).toFixed(3)} HYB ${mrr(hyb).toFixed(3)}`,
    )
  }
  console.log('')
  summarize('paraphrase', rows.filter((r) => r.type === 'paraphrase'))
  summarize('keyword', rows.filter((r) => r.type === 'keyword'))
  summarize('all', rows)
} finally {
  db.close()
  rmSync(tempDir, { recursive: true, force: true })
}
