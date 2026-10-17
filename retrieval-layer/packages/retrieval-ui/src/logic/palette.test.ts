import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createPaletteController, highlightSegments, moveIndex, parseQuery, type PaletteState } from './palette.ts'
import type { RetrievalClient, SearchHit } from '../types.ts'

const hit = (path: string): SearchHit => ({ path, startLine: 1, endLine: 3, symbol: null, score: 1, snippet: 'x' })
const deferred = <T>() => {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)))
  return { promise, resolve, reject }
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

test('parseQuery modes; # needs recall support', () => {
  assert.deepEqual(parseQuery('  foo bar ', true), { mode: 'search', term: 'foo bar' })
  assert.deepEqual(parseQuery('@Index', true), { mode: 'def', term: 'Index' })
  assert.deepEqual(parseQuery('# decision', true), { mode: 'recall', term: 'decision' })
  assert.deepEqual(parseQuery('#decision', false), { mode: 'search', term: '#decision' })
  assert.deepEqual(parseQuery('@', true), { mode: 'def', term: '' })
})

test('moveIndex wraps both ways and handles empty/none-selected', () => {
  assert.equal(moveIndex(-1, 1, 3), 0)
  assert.equal(moveIndex(-1, -1, 3), 2)
  assert.equal(moveIndex(2, 1, 3), 0)
  assert.equal(moveIndex(0, -1, 3), 2)
  assert.equal(moveIndex(0, 1, 1), 0)
  assert.equal(moveIndex(5, 1, 0), -1)
})

test('stale guard: a slower earlier response never overwrites a newer one', async () => {
  const first = deferred<SearchHit[]>()
  const second = deferred<SearchHit[]>()
  const pending = [first, second]
  const client: RetrievalClient = { search: () => pending.shift()!.promise, def: async () => [] }
  const states: PaletteState[] = []
  const c = createPaletteController(client, (s) => states.push(s), 5)
  c.setInput('old')
  await sleep(20)
  c.setInput('new')
  await sleep(20)
  second.resolve([hit('new.ts')])
  await sleep(5)
  first.resolve([hit('old.ts')])
  await sleep(5)
  assert.equal(c.getState().status, 'ready')
  assert.equal(c.getState().term, 'new')
  assert.deepEqual(c.getState().items.map((i) => (i.kind === 'location' ? i.path : '')), ['new.ts'])
})

test('stale guard also covers an older error arriving late', async () => {
  const first = deferred<SearchHit[]>()
  let call = 0
  const client: RetrievalClient = { search: () => (call++ === 0 ? first.promise : Promise.resolve([hit('b.ts')])), def: async () => [] }
  const c = createPaletteController(client, () => {}, 5)
  c.setInput('a')
  await sleep(20)
  c.setInput('b')
  await sleep(20)
  first.reject(new Error('boom'))
  await sleep(5)
  assert.equal(c.getState().status, 'ready')
})

test('debounce: rapid typing issues one request for the last value', async () => {
  const queries: string[] = []
  const client: RetrievalClient = { search: async (o) => (queries.push(o.query), []), def: async () => [] }
  const c = createPaletteController(client, () => {}, 30)
  for (const q of ['a', 'ab', 'abc']) c.setInput(q)
  await sleep(80)
  assert.deepEqual(queries, ['abc'])
})

test('clearing input cancels the pending request and returns to idle', async () => {
  const queries: string[] = []
  const client: RetrievalClient = { search: async (o) => (queries.push(o.query), []), def: async () => [] }
  const c = createPaletteController(client, () => {}, 20)
  c.setInput('abc')
  c.setInput('   ')
  await sleep(50)
  assert.deepEqual(queries, [])
  assert.equal(c.getState().status, 'idle')
})

test('rejection becomes error state; @ routes to def; # routes to recall only if present', async () => {
  const client: RetrievalClient = {
    search: async () => {
      throw new Error('index offline')
    },
    def: async (s) => [{ path: 'a.ts', line: 4, signature: `fn ${s}` }],
    recall: async () => [{ sessionId: 's', turnIndex: 2, role: 'user', createdAt: 0, score: 1, snippet: 'we chose postgres' }],
  }
  const c = createPaletteController(client, () => {}, 1)
  c.setInput('x')
  await sleep(20)
  assert.deepEqual([c.getState().status, c.getState().error], ['error', 'index offline'])
  c.setInput('@Thing')
  await sleep(20)
  assert.equal(c.getState().items[0]?.kind, 'location')
  c.setInput('#postgres')
  await sleep(20)
  assert.equal(c.getState().items[0]?.kind, 'recall')
})

test('dispose stops callbacks', async () => {
  let calls = 0
  const c = createPaletteController({ search: async () => [], def: async () => [] }, () => calls++, 1)
  c.setInput('x')
  const before = calls
  c.dispose()
  await sleep(20)
  assert.equal(calls, before)
})

test('highlightSegments: case-insensitive, regex metacharacters are literal, no lost text', () => {
  const text = 'Foo.bar(Foo) + foo'
  const segs = highlightSegments(text, 'foo.bar')
  assert.equal(segs.map((s) => s.text).join(''), text)
  assert.deepEqual(segs.filter((s) => s.match).map((s) => s.text), ['Foo.bar'])
  assert.deepEqual(highlightSegments('a+b', 'a+').filter((s) => s.match).map((s) => s.text), ['a+'])
  assert.deepEqual(highlightSegments('a<b>', '<b>').filter((s) => s.match).map((s) => s.text), ['<b>'])
  assert.deepEqual(highlightSegments('abc', ''), [{ text: 'abc', match: false }])
  assert.deepEqual(highlightSegments('', 'x'), [{ text: '', match: false }])
})
