import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRetrievalMcpServer, createRetrievalTools, RETRIEVAL_TOOL_NAMES } from '../src/agent-tools.ts'
import { indexWorkspace } from '../src/indexer.ts'
import { openStore } from '../src/store.ts'
import { createFakeChunker, createTempRepo, FIXTURE_REPO } from './helpers.ts'

type Handler = (args: any, extra: unknown) => Promise<{ isError?: boolean; content: { type: string; text?: string }[] }>

async function toolsFor(root: string) {
  const db = openStore(':memory:')
  await indexWorkspace(db, root, createFakeChunker())
  const byName = new Map(createRetrievalTools({ db, root }).map((t) => [t.name, t.handler as Handler]))
  const call = async (name: string, args: object) => {
    const result = await byName.get(name)!(args, undefined)
    return { isError: result.isError === true, text: result.content.map((c) => c.text ?? '').join('') }
  }
  return { db, call }
}

test('full tool names follow mcp__<server>__<tool>', () => {
  assert.deepEqual(RETRIEVAL_TOOL_NAMES, ['mcp__retrieval__search', 'mcp__retrieval__def', 'mcp__retrieval__read'])
  const server = createRetrievalMcpServer({ db: openStore(':memory:'), root: FIXTURE_REPO })
  assert.equal(server.type, 'sdk')
  assert.equal(server.name, 'retrieval')
})

test('search returns one compact block per hit: path:lines symbol (score) then snippet', async () => {
  const { call } = await toolsFor(FIXTURE_REPO)
  const { text, isError } = await call('search', { query: 'websocket auth token', k: 3 })
  assert.equal(isError, false)
  assert.match(text, /^src\/\S+:\d+-\d+  \S+ \(score \d+\.\d\)\n/)
  assert.ok(text.includes('src/server/websocket.ts'))
  assert.ok(!text.trimStart().startsWith('[') && !text.trimStart().startsWith('{'), 'plain text, not JSON')
})

test('search caps hits at 10 and snippets at 20 lines', async () => {
  const body = Array.from({ length: 40 }, (_, i) => `  const step${i} = ${i}`).join('\n')
  const files = Object.fromEntries(
    Array.from({ length: 15 }, (_, i) => [`src/f${i}.ts`, `export function widgetHandler${i}() {\n${body}\n}\n`]),
  )
  const repo = createTempRepo(files)
  try {
    const { call } = await toolsFor(repo.root)
    const { text } = await call('search', { query: 'widgetHandler step', k: 50 })
    const blocks = text.split('\n\n')
    assert.equal(blocks.length, 10)
    for (const block of blocks) assert.ok(block.split('\n').length <= 1 + 20, 'header plus at most 20 snippet lines')
  } finally {
    repo.cleanup()
  }
})

test('search with no match says so', async () => {
  const { call } = await toolsFor(FIXTURE_REPO)
  assert.equal((await call('search', { query: 'zzzqqqxxxnotpresent' })).text, 'No results.')
})

test('def returns path:line and signature', async () => {
  const { call } = await toolsFor(FIXTURE_REPO)
  const { text, isError } = await call('def', { symbol: 'IndexService' })
  assert.equal(isError, false)
  assert.match(text, /^src\/index\/indexService\.ts:\d+  export class IndexService \{$/)
})

test('def caps at 5 hits', async () => {
  const files = Object.fromEntries(Array.from({ length: 8 }, (_, i) => [`src/f${i}.ts`, 'export function dup() {}\n']))
  const repo = createTempRepo(files)
  try {
    const { call } = await toolsFor(repo.root)
    assert.equal((await call('def', { symbol: 'dup' })).text.split('\n').length, 5)
  } finally {
    repo.cleanup()
  }
})

test('read returns the requested range with a header and caps at 200 lines', async () => {
  const lines = Array.from({ length: 300 }, (_, i) => `line ${i + 1}`).join('\n')
  const repo = createTempRepo({ 'big.txt': lines })
  try {
    const { call } = await toolsFor(repo.root)
    const small = await call('read', { path: 'big.txt', startLine: 2, endLine: 4 })
    assert.equal(small.text, 'big.txt:2-4\nline 2\nline 3\nline 4')
    const big = await call('read', { path: 'big.txt', startLine: 1, endLine: 300 })
    assert.ok(big.text.startsWith('big.txt:1-200 (truncated at 200 lines)\n'))
    assert.equal(big.text.split('\n').length, 1 + 200)
  } finally {
    repo.cleanup()
  }
})

test('read path escape, absolute path, missing file and bad range return error results without throwing', async () => {
  const { call } = await toolsFor(FIXTURE_REPO)
  for (const args of [
    { path: '../../package.json', startLine: 1, endLine: 5 },
    { path: '/etc/passwd', startLine: 1, endLine: 5 },
    { path: 'src/nope.ts', startLine: 1, endLine: 5 },
    { path: 'README.md', startLine: 5, endLine: 1 },
  ]) {
    const result = await call('read', args)
    assert.equal(result.isError, true, JSON.stringify(args))
    assert.ok(result.text.length > 0)
  }
  assert.match((await call('read', { path: '../../package.json', startLine: 1, endLine: 5 })).text, /escapes the workspace/)
})
