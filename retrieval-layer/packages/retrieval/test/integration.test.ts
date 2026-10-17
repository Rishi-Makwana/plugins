import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { openStore, indexWorkspace, search, def, createChunker } from '../src/index.ts'

const fixtureRoot = fileURLToPath(new URL('./fixtures/repo', import.meta.url))

test('real chunker + indexer meet the Phase 1 acceptance checks', async () => {
  const db = openStore(':memory:')
  const stats = await indexWorkspace(db, fixtureRoot, createChunker())
  assert.ok(stats.filesIndexed > 0)
  assert.equal(stats.filesSkipped, 0)

  const defs = def(db, 'IndexService')
  assert.ok(defs.length > 0)
  assert.match(defs[0].path, /index/i)
  assert.match(defs[0].signature, /class IndexService/)

  const hits = search(db, { query: 'websocket auth token' })
  assert.ok(hits.slice(0, 3).some((h) => /socket|ws/i.test(h.path)), JSON.stringify(hits.map((h) => h.path)))

  const methodHits = search(db, { query: 'index service' })
  assert.ok(methodHits.some((h) => h.symbol?.startsWith('IndexService')))

  const unchanged = await indexWorkspace(db, fixtureRoot, createChunker())
  assert.equal(unchanged.filesIndexed, 0)
})
