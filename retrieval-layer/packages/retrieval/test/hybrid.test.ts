import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { createRetrievalTools } from '../src/agent-tools.ts'
import { indexChatTurn } from '../src/chat.ts'
import { HYBRID_CANDIDATES, hybridRecall, hybridSearch, reciprocalRankFusion, RRF_K } from '../src/hybrid.ts'
import { indexWorkspace, removeFile } from '../src/indexer.ts'
import { search } from '../src/search.ts'
import { identifierTerms, MIGRATIONS, openStore } from '../src/store.ts'
import type { Embedder } from '../src/types.ts'
import { embedPending, enableVectors } from '../src/vectors.ts'
import { createTransformersEmbedder } from '../src/embedder-transformers.ts'
import { createFakeChunker, createTempRepo } from './helpers.ts'
import type { TempRepo } from './helpers.ts'

const FAKE_DIMS = 64

// Hashed bag of words. `aliases` folds words onto one canonical word, standing in for "semantic" closeness.
function fakeEmbedder(id = 'fake-bow', aliases: Record<string, string> = {}, dims = FAKE_DIMS): Embedder & { calls: number; texts: number } {
  const embedder = {
    id,
    dims,
    calls: 0,
    texts: 0,
    async embed(texts: string[]) {
      embedder.calls++
      embedder.texts += texts.length
      return texts.map((text) => {
        const vector = new Float32Array(dims)
        for (const word of identifierTerms(text).split(' ')) {
          if (!word) continue
          let hash = 0
          for (const char of aliases[word] ?? word) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
          vector[hash % dims]! += 1
        }
        const norm = Math.hypot(...vector) || 1
        return vector.map((v) => v / norm)
      })
    },
  }
  return embedder
}

const repos: TempRepo[] = []
afterEach(() => {
  while (repos.length) repos.pop()!.cleanup()
})

async function vectorDb(files: Record<string, string>, embedder: Embedder = fakeEmbedder()) {
  const repo = createTempRepo(files)
  repos.push(repo)
  const db = openStore(':memory:')
  await indexWorkspace(db, repo.root, createFakeChunker())
  await enableVectors(db, { dims: embedder.dims, embedderId: embedder.id })
  return { repo, db, embedder }
}

const count = (db: ReturnType<typeof openStore>, table: string) =>
  (db.prepare(`select count(*) as n from ${table}`).get() as { n: number }).n

const FILES = {
  'src/auth.ts': 'export function issueToken(user) {\n  return sign(user)\n}\n',
  'src/walker.ts': 'export function walkDirectory(root) {\n  return list(root)\n}\n',
  'docs/guide.md': 'How to walk a directory tree and list files.\n',
}

test('vectors are not part of core migrations and not loaded by default', () => {
  assert.ok(!MIGRATIONS.join('\n').includes('chunks_vec'))
  const db = openStore(':memory:')
  assert.equal((db.prepare("select count(*) as n from sqlite_master where name = 'chunks_vec'").get() as { n: number }).n, 0)
})

test('enableVectors is idempotent and keeps stored vectors', async () => {
  const { db, embedder } = await vectorDb(FILES)
  await embedPending(db, embedder)
  const before = count(db, 'chunks_vec')
  assert.ok(before > 0)
  await enableVectors(db, { dims: embedder.dims, embedderId: embedder.id })
  assert.equal(count(db, 'chunks_vec'), before)
  assert.equal(count(db, 'vec_meta'), 2)
})

test('changing embedder id or dims drops and recreates vectors', async () => {
  const { db, embedder } = await vectorDb(FILES)
  await embedPending(db, embedder)
  await enableVectors(db, { dims: embedder.dims, embedderId: 'other-model' })
  assert.equal(count(db, 'chunks_vec'), 0)
  assert.equal(count(db, 'vec_chunks'), 0)
  const bigger = fakeEmbedder('other-model', {}, 96)
  await enableVectors(db, { dims: 96, embedderId: 'other-model' })
  await embedPending(db, bigger)
  assert.equal(count(db, 'chunks_vec'), count(db, 'chunks'))
})

test('embedPending embeds new chunks, skips existing, re-embeds edits, removes orphans', async () => {
  const { repo, db, embedder } = await vectorDb(FILES)
  const total = count(db, 'chunks')
  assert.equal((await embedPending(db, embedder)).embedded, total)
  assert.equal(count(db, 'chunks_vec'), total)

  const again = await embedPending(db, embedder)
  assert.deepEqual([again.embedded, again.removed], [0, 0])

  // walker.ts holds the highest chunk id, so its replacement chunk reuses that id: only the content hash reveals the edit.
  const maxIdBefore = (db.prepare('select max(id) as id from chunks').get() as { id: number }).id
  repo.write('src/walker.ts', 'export function walkDirectory(root) {\n  return listAgain(root)\n}\n')
  await indexWorkspace(db, repo.root, createFakeChunker())
  assert.equal((db.prepare('select max(id) as id from chunks').get() as { id: number }).id, maxIdBefore)
  assert.equal((await embedPending(db, embedder)).embedded, 1)

  repo.write('src/extra.ts', 'export function extra() {\n  return 1\n}\n')
  await indexWorkspace(db, repo.root, createFakeChunker())
  assert.equal((await embedPending(db, embedder)).embedded, 1)

  removeFile(db, 'docs/guide.md')
  const afterRemove = await embedPending(db, embedder)
  assert.equal(afterRemove.removed, 1)
  assert.equal(count(db, 'chunks_vec'), count(db, 'chunks'))
})

test('embedPending aborts between batches and resumes where it stopped', async () => {
  const files: Record<string, string> = {}
  for (let i = 0; i < 10; i++) files[`src/f${i}.ts`] = `export function fn${i}() {\n  return ${i}\n}\n`
  const { db, embedder } = await vectorDb(files)
  const controller = new AbortController()
  const first = await embedPending(db, embedder, {
    batchSize: 3,
    signal: controller.signal,
    onProgress: ({ embedded }) => {
      if (embedded >= 6) controller.abort()
    },
  })
  assert.equal(first.aborted, true)
  assert.equal(first.embedded, 6)
  assert.equal(count(db, 'chunks_vec'), 6)

  const second = await embedPending(db, embedder, { batchSize: 3 })
  assert.equal(second.aborted, false)
  assert.equal(second.embedded, 4)
  assert.equal(count(db, 'chunks_vec'), 10)
})

test('embedPending refuses an embedder that vectors were not enabled for', async () => {
  const { db } = await vectorDb(FILES)
  await assert.rejects(embedPending(db, fakeEmbedder('someone-else')), /enableVectors/)
})

test('RRF: hand-computed scores and order', () => {
  const fused = reciprocalRankFusion([[1, 2, 3], [3, 1]])
  const expected = new Map([
    [1, 1 / (RRF_K + 1) + 1 / (RRF_K + 2)],
    [3, 1 / (RRF_K + 3) + 1 / (RRF_K + 1)],
    [2, 1 / (RRF_K + 2)],
  ])
  assert.equal(RRF_K, 60)
  assert.deepEqual(fused.map((f) => f.id), [1, 3, 2])
  for (const { id, score } of fused) assert.ok(Math.abs(score - expected.get(id)!) < 1e-12)
})

test('hybrid finds a paraphrase that FTS misses', async () => {
  const embedder = fakeEmbedder('fake-alias', { directory: 'folder', walk: 'traverse', traverse: 'traverse', folder: 'folder', credential: 'token', mint: 'issue' })
  const { db } = await vectorDb(FILES, embedder)
  await embedPending(db, embedder)
  const query = 'mint a credential'
  assert.deepEqual(search(db, { query }), [], 'FTS has no shared words')
  const hits = await hybridSearch(db, embedder, { query }, () => assert.fail('should not fall back'))
  assert.equal(hits[0]?.path, 'src/auth.ts')
})

test('hybrid keeps FTS-strong hits and fuses both lists', async () => {
  const { db, embedder } = await vectorDb(FILES)
  await embedPending(db, embedder)
  const hits = await hybridSearch(db, embedder, { query: 'walkDirectory' })
  assert.equal(hits[0]!.path, 'src/walker.ts')
  assert.ok(hits[0]!.score > 0 && hits[0]!.score < 1)
})

test('falls back to plain search, and says so, when vectors are not enabled', async () => {
  const repo = createTempRepo(FILES)
  repos.push(repo)
  const db = openStore(':memory:')
  await indexWorkspace(db, repo.root, createFakeChunker())
  const reasons: string[] = []
  const hits = await hybridSearch(db, fakeEmbedder(), { query: 'issueToken' }, (reason) => reasons.push(reason))
  assert.deepEqual(hits, search(db, { query: 'issueToken' }))
  assert.equal(reasons.length, 1)
})

test('falls back instead of throwing when the embedder fails', async () => {
  const { db, embedder } = await vectorDb(FILES)
  await embedPending(db, embedder)
  const broken: Embedder = { ...embedder, embed: async () => Promise.reject(new Error('model gone')) }
  const reasons: string[] = []
  const hits = await hybridSearch(db, broken, { query: 'issueToken' }, (reason) => reasons.push(reason))
  assert.deepEqual(hits, search(db, { query: 'issueToken' }))
  assert.deepEqual(reasons, ['model gone'])
})

test('filters: pathPrefix, kinds, default excludes chat, k is capped at 10', async () => {
  const files: Record<string, string> = { 'docs/notes.md': 'token notes for humans\n' }
  for (let i = 0; i < 15; i++) files[`src/t${i}.ts`] = `export function tokenHelper${i}() {\n  return 'token'\n}\n`
  const { db, embedder } = await vectorDb(files)
  indexChatTurn(db, { sessionId: 's1', turnIndex: 0, role: 'user', text: 'secret token decision', createdAt: 1 })
  await embedPending(db, embedder)

  const all = await hybridSearch(db, embedder, { query: 'token', k: 50 })
  assert.equal(all.length, 10)
  assert.ok(all.every((hit) => !hit.path.startsWith('chat/')))

  const docs = await hybridSearch(db, embedder, { query: 'token', kinds: ['doc'] })
  assert.deepEqual(docs.map((hit) => hit.path), ['docs/notes.md'])

  const prefixed = await hybridSearch(db, embedder, { query: 'token', pathPrefix: 'src/t1' })
  assert.ok(prefixed.length > 0 && prefixed.every((hit) => hit.path.startsWith('src/t1')))

  const chat = await hybridSearch(db, embedder, { query: 'token', kinds: ['chat'] })
  assert.deepEqual(chat.map((hit) => hit.path), ['chat/s1'])
})

test('hybridRecall returns chat only and respects sessionId', async () => {
  const { db, embedder } = await vectorDb(FILES)
  indexChatTurn(db, { sessionId: 's1', turnIndex: 0, role: 'user', text: 'we chose postgres', createdAt: 1 })
  indexChatTurn(db, { sessionId: 's2', turnIndex: 0, role: 'user', text: 'we chose sqlite', createdAt: 2 })
  await embedPending(db, embedder)
  const hits = await hybridRecall(db, embedder, { query: 'we chose database', sessionId: 's2' })
  assert.deepEqual(hits.map((hit) => hit.sessionId), ['s2'])
})

test('HYBRID_CANDIDATES is the documented 50', () => assert.equal(HYBRID_CANDIDATES, 50))

test('agent search tool uses hybrid when an embedder is given, tool names unchanged', async () => {
  const embedder = fakeEmbedder('fake-alias', { credential: 'token', mint: 'issue' })
  const { db, repo } = await vectorDb(FILES, embedder)
  await embedPending(db, embedder)
  const find = (tools: ReturnType<typeof createRetrievalTools>) => tools.find((t) => t.name === 'search')!.handler as (a: object, e: unknown) => Promise<{ content: { text?: string }[] }>
  const plain = await find(createRetrievalTools({ db, root: repo.root }))({ query: 'mint a credential' }, undefined)
  const hybrid = await find(createRetrievalTools({ db, root: repo.root, embedder }))({ query: 'mint a credential' }, undefined)
  assert.equal(plain.content[0]!.text, 'No results.')
  assert.match(hybrid.content[0]!.text!, /src\/auth\.ts/)
  assert.deepEqual(createRetrievalTools({ db, root: repo.root, embedder }).map((t) => t.name), ['search', 'def', 'read', 'recall'])
})

test('real Transformers.js model embeds and ranks a paraphrase', { skip: process.env.RETRIEVAL_REAL_MODEL !== '1', timeout: 300_000 }, async () => {
  const embedder = createTransformersEmbedder({ cacheDir: process.env.RETRIEVAL_MODEL_CACHE })
  const { db } = await vectorDb(FILES, embedder)
  await embedPending(db, embedder)
  const hits = await hybridSearch(db, embedder, { query: 'create a credential for a user' })
  assert.equal(hits[0]?.path, 'src/auth.ts')
})
