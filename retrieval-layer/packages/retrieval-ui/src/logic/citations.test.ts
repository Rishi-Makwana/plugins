import assert from 'node:assert/strict'
import { test } from 'node:test'
import { extractCitationsFromTurn, parseCitations } from './citations.ts'

const SEARCH = [
  'src/server.ts:10-42  connectHandler (score 3.1)',
  'export function connectHandler() {',
  '',
  '  return 1',
  '}',
  '',
  'docs/guide.md:1-20  - (score 1.2)',
  '# Guide',
].join('\n')

test('search: headers parsed, snippet with blank lines does not break parsing', () => {
  assert.deepEqual(parseCitations('mcp__retrieval__search', SEARCH), [
    { path: 'src/server.ts', startLine: 10, endLine: 42, symbol: 'connectHandler' },
    { path: 'docs/guide.md', startLine: 1, endLine: 20 },
  ])
})

test('search: CRLF output parses the same', () => {
  assert.equal(parseCitations('mcp__retrieval__search', SEARCH.replaceAll('\n', '\r\n')).length, 2)
})

test('search: unicode paths and paths with spaces and colons', () => {
  const text = 'src/日本語/ファイル.ts:3-9  f (score 1.0)\nmy dir/a:b file.ts:5-6  - (score 0.5)'
  assert.deepEqual(
    parseCitations('mcp__retrieval__search', text).map((c) => c.path),
    ['src/日本語/ファイル.ts', 'my dir/a:b file.ts'],
  )
})

test('search: duplicates collapse', () => {
  const header = 'a.ts:1-2  x (score 1.0)'
  assert.equal(parseCitations('mcp__retrieval__search', `${header}\nbody\n\n${header}\nbody`).length, 1)
})

test('empty, garbled and "No results." output yield nothing', () => {
  for (const text of ['', 'No results.', 'garbage:::\n--\n:1-2', 'a.ts:0-5  x (score 1)', 'a.ts:9-2  x (score 1)']) {
    assert.deepEqual(parseCitations('mcp__retrieval__search', text), [], text)
  }
  assert.deepEqual(parseCitations('mcp__retrieval__search', undefined as unknown as string), [])
})

test('def: path:line lines, single-line citations', () => {
  const text = 'src/index.ts:12  export class IndexService {\nsrc/other file.ts:7  function IndexService()'
  assert.deepEqual(parseCitations('mcp__retrieval__def', text), [
    { path: 'src/index.ts', startLine: 12 },
    { path: 'src/other file.ts', startLine: 7 },
  ])
})

test('read: only the header line counts, truncated suffix accepted, file body ignored', () => {
  const body = 'x.ts:1-200 (truncated at 200 lines)\nfoo.ts:5-6  y (score 1.0)'
  assert.deepEqual(parseCitations('mcp__retrieval__read', 'src/a.ts:10-30\nconst a = 1'), [{ path: 'src/a.ts', startLine: 10, endLine: 30 }])
  assert.deepEqual(parseCitations('mcp__retrieval__read', body), [{ path: 'x.ts', startLine: 1, endLine: 200 }])
  assert.deepEqual(parseCitations('mcp__retrieval__read', 'src/a.ts: no lines in range 5-9.'), [])
})

test('recall and unknown tools yield nothing; virtual chat/ paths are dropped', () => {
  assert.deepEqual(parseCitations('mcp__retrieval__recall', 'session s1 turn 3  user 2026-01-01T00:00:00.000Z (score 1.0)\nhi'), [])
  assert.deepEqual(parseCitations('Grep', 'a.ts:1-2  x (score 1.0)'), [])
  assert.deepEqual(parseCitations('mcp__retrieval__search', 'chat/abc123:1-4  - (score 1.0)'), [])
})

test('turn: links tool_result to tool_use by id, skips errors and non-retrieval tools, dedupes across calls', () => {
  const messages = [
    { type: 'assistant', message: { content: [{ type: 'text', text: 'looking' }, { type: 'tool_use', id: 't1', name: 'mcp__retrieval__search', input: {} }, { type: 'tool_use', id: 't2', name: 'Grep', input: {} }, { type: 'tool_use', id: 't3', name: 'mcp__retrieval__read', input: {} }] } },
    {
      type: 'user',
      message: {
        content: [
          { type: 'tool_result', tool_use_id: 't1', content: [{ type: 'text', text: 'a.ts:1-5  f (score 1.0)\nbody' }] },
          { type: 'tool_result', tool_use_id: 't2', content: 'b.ts:1-5  f (score 1.0)' },
          { type: 'tool_result', tool_use_id: 't3', content: 'a.ts:1-5\nbody' },
          { type: 'tool_result', tool_use_id: 'missing', content: 'c.ts:1-5  f (score 1.0)' },
        ],
      },
    },
    { type: 'user', message: { content: 'plain string content' } },
    { type: 'system' },
  ]
  assert.deepEqual(extractCitationsFromTurn(messages), [{ path: 'a.ts', startLine: 1, endLine: 5, symbol: 'f' }])
})

test('turn: is_error results, string content, malformed blocks and empty input are safe', () => {
  assert.deepEqual(extractCitationsFromTurn([]), [])
  const messages = [
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 'e', name: 'mcp__retrieval__def' }, null, 7] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 'e', is_error: true, content: 'a.ts:1  x' }, { type: 'tool_result' }] } },
  ]
  assert.deepEqual(extractCitationsFromTurn(messages as never), [])
})
