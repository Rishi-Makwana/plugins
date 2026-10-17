import assert from 'node:assert/strict'
import { readFileSync, symlinkSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, before, test } from 'node:test'
import { indexWorkspace } from '../src/indexer.ts'
import { buildMatchQuery, def, readRange, search } from '../src/search.ts'
import { openStore } from '../src/store.ts'
import type { Chunker } from '../src/types.ts'
import { createFakeChunker, createTempRepo, FIXTURE_REPO } from './helpers.ts'
import type { TempRepo } from './helpers.ts'

const repos: TempRepo[] = []
afterEach(() => {
  while (repos.length) repos.pop()!.cleanup()
})
async function indexedTempRepo(files: Record<string, string | Buffer>, chunker: Chunker = createFakeChunker()) {
  const repo = createTempRepo(files)
  repos.push(repo)
  const db = openStore(':memory:')
  await indexWorkspace(db, repo.root, chunker)
  return { repo, db }
}

let fixtureDb: ReturnType<typeof openStore>
before(async () => {
  fixtureDb = openStore(':memory:')
  await indexWorkspace(fixtureDb, FIXTURE_REPO, createFakeChunker())
})

test('plan acceptance: def(IndexService) returns the defining file and line', () => {
  const source = readFileSync(join(FIXTURE_REPO, 'src/index/indexService.ts'), 'utf8').split('\n')
  const line = source.findIndex((text) => text.startsWith('export class IndexService')) + 1
  assert.ok(line > 0)
  const hits = def(fixtureDb, 'IndexService')
  assert.equal(hits.length, 1)
  assert.equal(hits[0]!.path, 'src/index/indexService.ts')
  assert.equal(hits[0]!.line, line)
  assert.equal(hits[0]!.signature, 'export class IndexService {')
})

test('plan acceptance: "websocket auth token" puts the connect handler in the top 3', () => {
  const hits = search(fixtureDb, { query: 'websocket auth token' })
  const rank = hits.findIndex((hit) => hit.path === 'src/server/websocket.ts' && hit.symbol === 'handleConnect')
  assert.ok(rank >= 0 && rank < 3, `handleConnect ranked ${rank}: ${hits.map((h) => `${h.path}#${h.symbol}`).join(', ')}`)
  assert.ok(hits[0]!.score > 0)
})

test('plan acceptance: "index service" finds IndexService through identifier terms', () => {
  const hits = search(fixtureDb, { query: 'index service' })
  assert.ok(hits.slice(0, 3).some((hit) => hit.symbol === 'IndexService'), hits.map((h) => `${h.path}#${h.symbol}`).join(', '))
})

test('a lone sub-word of an identifier finds it only through terms', () => {
  const hits = search(fixtureDb, { query: 'service' })
  assert.equal(hits[0]?.symbol, 'IndexService')
})

test('def: methods match by .name suffix, then prefix; non-symbol chunks never match', async () => {
  const { db } = await indexedTempRepo({ 'a.ts': 'export function parseThing() {}\n', 'notes.md': 'parseThing is documented here\n' })
  db.prepare(
    "insert into chunks (path, kind, symbol, symbol_kind, start_line, end_line, text, terms) values ('m.ts','code','Widget.render','method',4,6,'   render() {\n}','')",
  ).run()
  assert.deepEqual(def(db, 'render').map((h) => h.path), ['m.ts'])
  assert.deepEqual(def(db, 'parse').map((h) => `${h.path}:${h.line}`), ['a.ts:1'])
  assert.deepEqual(def(db, 'nothing'), [])
  assert.deepEqual(def(db, ''), [])
  assert.deepEqual(def(db, '%'), [])
  assert.equal(def(db, 'render')[0]!.signature, 'render() {')
})

test('def caps hits at DEF_MAX_HITS and dedupes path+line', async () => {
  const files: Record<string, string> = {}
  for (let i = 0; i < 9; i++) files[`f${i}.ts`] = `export function run${i}() {}\n`
  const { db } = await indexedTempRepo(files)
  assert.equal(def(db, 'run').length, 5)
  db.prepare(
    "insert into chunks (path, kind, symbol, symbol_kind, start_line, end_line, text, terms) values ('f0.ts','code','run0','fn',1,1,'dup','')",
  ).run()
  assert.equal(def(db, 'run0').length, 1)
})

test('search never throws on FTS syntax characters or hostile input', async () => {
  const { db } = await indexedTempRepo({ 'a.ts': 'export function hello() { return "world" }\n' })
  const hostile = ['"', '""', 'hello"', '*', 'hel*', 'AND', 'OR', 'NOT', 'hello AND', '(', ')', '(hello', '-', '-hello', 'NEAR', 'NEAR(a b)', 'a NEAR/2 b', 'col:hello', '^hello', "'; drop table chunks; --", '\\', '\u0000', '{}[]']
  for (const query of hostile) {
    assert.doesNotThrow(() => search(db, { query }), `query ${JSON.stringify(query)}`)
  }
  assert.equal(search(db, { query: '"hello" AND (world' })[0]?.path, 'a.ts')
  assert.equal((db.prepare('select count(*) as n from chunks').get() as { n: number }).n, 1)
})

test('empty, whitespace and too-short queries return []', async () => {
  const { db } = await indexedTempRepo({ 'a.ts': 'export function hello() {}\n' })
  for (const query of ['', '   ', 'a', '- - -', '*']) assert.deepEqual(search(db, { query }), [], JSON.stringify(query))
  assert.equal(buildMatchQuery('say "hi"'), '"say" OR "hi"')
})

test('k defaults to 8, clamps up to 1 and down to SEARCH_MAX_K', async () => {
  const files: Record<string, string> = {}
  for (let i = 0; i < 15; i++) files[`f${i}.ts`] = `export function needle${i}() { return 'needle' }\n`
  const { db } = await indexedTempRepo(files)
  assert.equal(search(db, { query: 'needle' }).length, 8)
  assert.equal(search(db, { query: 'needle', k: 0 }).length, 1)
  assert.equal(search(db, { query: 'needle', k: -5 }).length, 1)
  assert.equal(search(db, { query: 'needle', k: 100 }).length, 10)
  assert.equal(search(db, { query: 'needle', k: 3 }).length, 3)
})

test('kinds filter separates code from docs', async () => {
  const { db } = await indexedTempRepo({ 'a.ts': 'export function widget() {}\n', 'a.md': 'The widget is documented.\n' })
  assert.deepEqual(search(db, { query: 'widget', kinds: ['doc'] }).map((h) => h.path), ['a.md'])
  assert.deepEqual(search(db, { query: 'widget', kinds: ['code'] }).map((h) => h.path), ['a.ts'])
  assert.equal(search(db, { query: 'widget', kinds: ['chat'] }).length, 0)
  assert.equal(search(db, { query: 'widget', kinds: [] }).length, 2)
})

test('pathPrefix escapes LIKE wildcards % and _', async () => {
  const { db } = await indexedTempRepo({
    'a_b/x.ts': 'export function needle() {}\n',
    'aXb/y.ts': 'export function needle() {}\n',
    'a%b/z.ts': 'export function needle() {}\n',
    'azzb/w.ts': 'export function needle() {}\n',
  })
  const paths = (pathPrefix: string) => search(db, { query: 'needle', pathPrefix }).map((h) => h.path).sort()
  assert.deepEqual(paths('a_b/'), ['a_b/x.ts'])
  assert.deepEqual(paths('a%b/'), ['a%b/z.ts'])
  assert.deepEqual(paths('%'), [])
  assert.deepEqual(paths('_'), [])
  assert.deepEqual(paths('a'), ['a%b/z.ts', 'aXb/y.ts', 'a_b/x.ts', 'azzb/w.ts'])
})

test('snippet is capped at SNIPPET_MAX_LINES lines', async () => {
  const body = Array.from({ length: 50 }, (_, i) => `  const line${i} = marker`).join('\n')
  const { db } = await indexedTempRepo({ 'a.ts': `export function long() {\n${body}\n}\n` })
  const [hit] = search(db, { query: 'marker' })
  assert.equal(hit!.snippet.split('\n').length, 20)
  assert.equal(hit!.endLine, 52)
})

test('when a hit contains another hit in the same file, only the smaller one is returned', async () => {
  const overlapping: Chunker = {
    async chunkFile() {
      return {
        kind: 'code',
        chunks: [
          { symbol: null, symbolKind: null, startLine: 1, endLine: 10, text: 'prelude\nneedle inside\nrest' },
          { symbol: 'inner', symbolKind: 'fn', startLine: 2, endLine: 3, text: 'needle inside' },
        ],
      }
    },
  }
  const { db } = await indexedTempRepo({ 'a.ts': 'x\n', 'b.ts': 'y\n' }, overlapping)
  const hits = search(db, { query: 'needle' })
  assert.deepEqual(hits.map((h) => `${h.path}:${h.startLine}-${h.endLine}`).sort(), ['a.ts:2-3', 'b.ts:2-3'])
})

test('readRange returns the requested lines and caps at 200', async () => {
  const lines = Array.from({ length: 500 }, (_, i) => `line ${i + 1}`)
  const repo = createTempRepo({ 'big.txt': lines.join('\n'), 'sub/crlf.txt': 'a\r\nb\r\nc' })
  repos.push(repo)
  assert.equal(readRange(repo.root, 'big.txt', 2, 4), 'line 2\nline 3\nline 4')
  const capped = readRange(repo.root, 'big.txt', 1, 500).split('\n')
  assert.equal(capped.length, 200)
  assert.equal(capped.at(-1), 'line 200')
  assert.equal(readRange(repo.root, 'big.txt', 499, 900), 'line 499\nline 500')
  assert.equal(readRange(repo.root, 'big.txt', 900, 950), '')
  assert.equal(readRange(repo.root, 'sub/crlf.txt', 1, 2), 'a\nb')
})

test('readRange rejects traversal, absolute paths, symlink escapes and bad ranges', () => {
  const outside = createTempRepo({ 'secret.txt': 'top secret' })
  const repo = createTempRepo({ 'ok.txt': 'fine', 'sub/ok.txt': 'fine' })
  repos.push(outside, repo)
  symlinkSync(outside.root, join(repo.root, 'link'))
  for (const path of ['../etc/passwd', '../../etc/passwd', 'sub/../../etc/passwd', '/etc/passwd', join(outside.root, 'secret.txt'), 'link/secret.txt', '', '.', 'ok.txt\0.png']) {
    assert.throws(() => readRange(repo.root, path, 1, 5), /readRange/, JSON.stringify(path))
  }
  assert.equal(readRange(repo.root, 'sub/../ok.txt', 1, 1), 'fine')
  assert.throws(() => readRange(repo.root, 'ok.txt', 0, 5), /invalid line range/)
  assert.throws(() => readRange(repo.root, 'ok.txt', 5, 2), /invalid line range/)
  assert.throws(() => readRange(repo.root, 'ok.txt', 1.5, 2), /invalid line range/)
})
