import assert from 'node:assert/strict'
import { test } from 'node:test'
import { forgetSession, indexChatTurn } from '../src/chat.ts'
import { indexWorkspace } from '../src/indexer.ts'
import { recall, search } from '../src/search.ts'
import { openStore } from '../src/store.ts'
import { createFakeChunker, createTempRepo } from './helpers.ts'

const turn = (over: Partial<Parameters<typeof indexChatTurn>[1]> = {}) => ({
  sessionId: 's1',
  turnIndex: 0,
  role: 'user' as const,
  text: 'we decided to use postgres for storage',
  createdAt: 1_700_000_000_000,
  ...over,
})
const countChunks = (db: ReturnType<typeof openStore>) =>
  (db.prepare("select count(*) as n from chunks where kind = 'chat'").get() as { n: number }).n

test('recall returns the turn with session, turn, role, createdAt and snippet', () => {
  const db = openStore(':memory:')
  indexChatTurn(db, turn())
  indexChatTurn(db, turn({ turnIndex: 1, role: 'assistant', text: 'ok, postgres it is', createdAt: 1_700_000_001_000 }))
  const hits = recall(db, { query: 'postgres storage' })
  assert.equal(hits.length, 2)
  const first = hits.find((h) => h.turnIndex === 0)!
  assert.deepEqual(
    { sessionId: first.sessionId, role: first.role, createdAt: first.createdAt, snippet: first.snippet },
    { sessionId: 's1', role: 'user', createdAt: 1_700_000_000_000, snippet: 'we decided to use postgres for storage' },
  )
  assert.ok(first.score > 0)
})

test('re-indexing the same (session, turn, role) replaces chunks; other roles and turns survive', () => {
  const db = openStore(':memory:')
  indexChatTurn(db, turn({ text: 'old text zebra' }))
  indexChatTurn(db, turn({ role: 'assistant', text: 'assistant zebra reply' }))
  indexChatTurn(db, turn({ text: 'new text giraffe' }))
  assert.equal(countChunks(db), 2)
  assert.equal(recall(db, { query: 'zebra' }).length, 1)
  assert.equal(recall(db, { query: 'giraffe' }).length, 1)
  indexChatTurn(db, turn({ text: 'new text giraffe' }))
  assert.equal(countChunks(db), 2)
})

test('forgetSession deletes only that session, including its search index entries', () => {
  const db = openStore(':memory:')
  indexChatTurn(db, turn({ text: 'secret walrus' }))
  indexChatTurn(db, turn({ sessionId: 's2', text: 'other walrus' }))
  forgetSession(db, 's1')
  const hits = recall(db, { query: 'walrus' })
  assert.deepEqual(hits.map((h) => h.sessionId), ['s2'])
  assert.equal(
    (db.prepare(`select count(*) as n from chunks_fts where chunks_fts match '"secret"'`).get() as { n: number }).n,
    0,
  )
})

test('recall can be scoped to one session', () => {
  const db = openStore(':memory:')
  indexChatTurn(db, turn({ text: 'walrus one' }))
  indexChatTurn(db, turn({ sessionId: 's2', text: 'walrus two' }))
  assert.deepEqual(recall(db, { query: 'walrus', sessionId: 's2' }).map((h) => h.sessionId), ['s2'])
})

test('recall excludes code and docs; search excludes chat by default but not when asked', async () => {
  const repo = createTempRepo({ 'a.ts': 'export function walrusCode() {}\n', 'a.md': 'walrus docs\n' })
  try {
    const db = openStore(':memory:')
    await indexWorkspace(db, repo.root, createFakeChunker())
    indexChatTurn(db, turn({ text: 'walrus chat' }))
    assert.deepEqual(recall(db, { query: 'walrus' }).map((h) => h.snippet), ['walrus chat'])
    const defaultPaths = search(db, { query: 'walrus' }).map((h) => h.path).sort()
    assert.deepEqual(defaultPaths, ['a.md', 'a.ts'])
    assert.deepEqual(search(db, { query: 'walrus', kinds: ['chat'] }).map((h) => h.path), ['chat/s1'])
  } finally {
    repo.cleanup()
  }
})

test('long messages split into 60-line windows that share the turn index', () => {
  const db = openStore(':memory:')
  const text = Array.from({ length: 130 }, (_, i) => `line${i} marmot`).join('\n')
  assert.equal(indexChatTurn(db, turn({ turnIndex: 7, text })), 3)
  const rows = db.prepare("select start_line, end_line, text from chunks where kind = 'chat' order by id").all() as {
    start_line: number
    end_line: number
    text: string
  }[]
  assert.deepEqual(rows.map((r) => [r.start_line, r.end_line]), [[7, 7], [7, 7], [7, 7]])
  assert.deepEqual(rows.map((r) => r.text.split('\n').length), [60, 60, 10])
  assert.ok(recall(db, { query: 'marmot' }).every((h) => h.turnIndex === 7))
})

test('unicode text is searchable', () => {
  const db = openStore(':memory:')
  indexChatTurn(db, turn({ text: 'Entscheidung: Überprüfung der Straße 日本語 café' }))
  assert.equal(recall(db, { query: 'Überprüfung' }).length, 1)
  assert.equal(recall(db, { query: 'café' }).length, 1)
})

test('empty or whitespace text stores nothing and clears the previous version', () => {
  const db = openStore(':memory:')
  indexChatTurn(db, turn())
  assert.equal(indexChatTurn(db, turn({ text: '' })), 0)
  assert.equal(countChunks(db), 0)
  assert.equal(indexChatTurn(db, turn({ text: '  \n ' })), 0)
  assert.deepEqual(recall(db, { query: 'postgres' }), [])
})

test('chat path is virtual and re-indexing the workspace leaves chat chunks alone', async () => {
  const repo = createTempRepo({ 'a.md': 'hello\n' })
  try {
    const db = openStore(':memory:')
    indexChatTurn(db, turn())
    await indexWorkspace(db, repo.root, createFakeChunker())
    assert.equal(countChunks(db), 1)
    assert.equal((db.prepare('select path from chunks where kind = ?').get('chat') as { path: string }).path, 'chat/s1')
  } finally {
    repo.cleanup()
  }
})

test('recall with an unusable query returns nothing', () => {
  assert.deepEqual(recall(openStore(':memory:'), { query: '!!' }), [])
})
