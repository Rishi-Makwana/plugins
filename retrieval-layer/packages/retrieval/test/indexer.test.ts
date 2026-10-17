import assert from 'node:assert/strict'
import { symlinkSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, test } from 'node:test'
import { indexFile, indexWorkspace, removeFile } from '../src/indexer.ts'
import { openStore } from '../src/store.ts'
import { createFakeChunker, createTempRepo, FIXTURE_REPO } from './helpers.ts'
import type { TempRepo } from './helpers.ts'

const repos: TempRepo[] = []
afterEach(() => {
  while (repos.length) repos.pop()!.cleanup()
})
function tempRepo(files: Record<string, string | Buffer> = {}): TempRepo {
  const repo = createTempRepo(files)
  repos.push(repo)
  return repo
}

const indexedPaths = (db: ReturnType<typeof openStore>) =>
  (db.prepare('select path from indexed_files order by path').all() as { path: string }[]).map((row) => row.path)

test('empty repo indexes nothing', async () => {
  const db = openStore(':memory:')
  const stats = await indexWorkspace(db, tempRepo().root, createFakeChunker())
  assert.deepEqual(
    { ...stats, ms: 0 },
    { filesSeen: 0, filesIndexed: 0, filesUnchanged: 0, filesRemoved: 0, filesSkipped: 0, chunks: 0, ms: 0 },
  )
})

test('one file is chunked and stored with relative path and terms', async () => {
  const repo = tempRepo({ 'src/a.ts': 'export function fooBar() {\n  return 1\n}\n' })
  const db = openStore(':memory:')
  const stats = await indexWorkspace(db, repo.root, createFakeChunker())
  assert.equal(stats.filesIndexed, 1)
  assert.equal(stats.chunks, 1)
  const row = db.prepare('select path, symbol, symbol_kind, start_line, end_line, terms from chunks').get() as Record<string, unknown>
  assert.equal(row.path, 'src/a.ts')
  assert.equal(row.symbol, 'fooBar')
  assert.equal(row.symbol_kind, 'fn')
  assert.equal(row.start_line, 1)
  assert.match(String(row.terms), /\bfoo\b.*\bbar\b/)
})

test('root and nested .gitignore are respected, nested ones relative to their own directory', async () => {
  const repo = tempRepo({
    '.gitignore': '*.log\nbuild/\n',
    'keep.ts': 'export function keep() {}\n',
    'debug.log': 'noise',
    'build/out.ts': 'export function out() {}\n',
    'pkg/.gitignore': 'secret.ts\n/local.ts\n',
    'pkg/secret.ts': 'export function secret() {}\n',
    'pkg/local.ts': 'export function local() {}\n',
    'pkg/sub/local.ts': 'export function nestedLocal() {}\n',
    'other/secret.ts': 'export function otherSecret() {}\n',
    'pkg/real.ts': 'export function real() {}\n',
  })
  const db = openStore(':memory:')
  await indexWorkspace(db, repo.root, createFakeChunker())
  assert.deepEqual(indexedPaths(db), ['keep.ts', 'other/secret.ts', 'pkg/real.ts', 'pkg/sub/local.ts'])
})

test('.git, node_modules and symlinked directories are never walked', async () => {
  const repo = tempRepo({
    'a.ts': 'export function a() {}\n',
    '.git/config.ts': 'export function gitInternal() {}\n',
    'node_modules/dep/index.ts': 'export function dep() {}\n',
    'real/b.ts': 'export function b() {}\n',
  })
  symlinkSync(repo.root, join(repo.root, 'loop'))
  symlinkSync(join(repo.root, 'real'), join(repo.root, 'alias'))
  const db = openStore(':memory:')
  await indexWorkspace(db, repo.root, createFakeChunker())
  assert.deepEqual(indexedPaths(db), ['a.ts', 'real/b.ts'])
})

test('binary files, oversized files and unsupported types are skipped and counted', async () => {
  const repo = tempRepo({
    'ok.ts': 'export function ok() {}\n',
    'image.ts': Buffer.concat([Buffer.from('export function x() {}'), Buffer.from([0, 1, 2])]),
    'big.txt': 'a\n'.repeat(600_000),
    'data.bin': 'text but unsupported',
  })
  const db = openStore(':memory:')
  const chunker = createFakeChunker()
  const stats = await indexWorkspace(db, repo.root, chunker)
  assert.equal(stats.filesSeen, 4)
  assert.equal(stats.filesIndexed, 1)
  assert.equal(stats.filesSkipped, 3)
  assert.deepEqual(indexedPaths(db), ['ok.ts'])
  assert.ok(!chunker.calls.includes('image.ts') && !chunker.calls.includes('big.txt'), 'binary/oversized never reach the chunker')
})

test('unicode filename and content are indexed', async () => {
  const repo = tempRepo({ 'docs/привет-文档.md': 'Здравствуй мир\nnaïve café\n' })
  const db = openStore(':memory:')
  const stats = await indexWorkspace(db, repo.root, createFakeChunker())
  assert.equal(stats.filesIndexed, 1)
  const row = db.prepare('select path, text from chunks').get() as { path: string; text: string }
  assert.equal(row.path.normalize('NFC'), 'docs/привет-文档.md')
  assert.match(row.text, /Здравствуй мир/)
})

test('unchanged re-run indexes nothing and does not touch chunks', async () => {
  const repo = tempRepo({ 'a.ts': 'export function a() {}\n', 'b.ts': 'export function b() {}\n' })
  const db = openStore(':memory:')
  const chunker = createFakeChunker()
  await indexWorkspace(db, repo.root, chunker)
  const idsBefore = db.prepare('select id from chunks order by id').all()
  chunker.calls.length = 0
  const stats = await indexWorkspace(db, repo.root, chunker)
  assert.equal(stats.filesIndexed, 0)
  assert.equal(stats.filesUnchanged, 2)
  assert.deepEqual(chunker.calls, [])
  assert.deepEqual(db.prepare('select id from chunks order by id').all(), idsBefore)
})

test('edited file is re-chunked alone; stale chunks are gone', async () => {
  const repo = tempRepo({ 'a.ts': 'export function alpha() {}\n', 'b.ts': 'export function beta() {}\n' })
  const db = openStore(':memory:')
  const chunker = createFakeChunker()
  await indexWorkspace(db, repo.root, chunker)
  const bIdBefore = db.prepare("select id from chunks where path = 'b.ts'").get()
  repo.write('a.ts', 'export function gamma() {}\nexport function delta() {}\n')
  chunker.calls.length = 0
  const stats = await indexWorkspace(db, repo.root, chunker)
  assert.equal(stats.filesIndexed, 1)
  assert.equal(stats.filesUnchanged, 1)
  assert.deepEqual(chunker.calls, ['a.ts'])
  assert.equal(stats.chunks, 3)
  assert.deepEqual(db.prepare("select id from chunks where path = 'b.ts'").get(), bIdBefore)
  const symbols = (db.prepare("select symbol from chunks where path = 'a.ts' order by start_line").all() as { symbol: string }[]).map((r) => r.symbol)
  assert.deepEqual(symbols, ['gamma', 'delta'])
})

test('file deleted between runs, or newly ignored, is removed with its chunks', async () => {
  const repo = tempRepo({ 'a.ts': 'export function a() {}\n', 'b.ts': 'export function b() {}\n', 'c.ts': 'export function c() {}\n' })
  const db = openStore(':memory:')
  const chunker = createFakeChunker()
  await indexWorkspace(db, repo.root, chunker)
  repo.remove('a.ts')
  repo.write('.gitignore', 'c.ts\n')
  const stats = await indexWorkspace(db, repo.root, chunker)
  assert.equal(stats.filesRemoved, 2)
  assert.deepEqual(indexedPaths(db), ['b.ts'])
  assert.equal((db.prepare('select count(*) as n from chunks').get() as { n: number }).n, 1)
})

test('a file that becomes binary loses its stale chunks', async () => {
  const repo = tempRepo({ 'a.ts': 'export function a() {}\n' })
  const db = openStore(':memory:')
  const chunker = createFakeChunker()
  await indexWorkspace(db, repo.root, chunker)
  repo.write('a.ts', Buffer.from([0, 0, 0]))
  const stats = await indexWorkspace(db, repo.root, chunker)
  assert.equal(stats.filesSkipped, 1)
  assert.deepEqual(indexedPaths(db), [])
  assert.equal((db.prepare('select count(*) as n from chunks').get() as { n: number }).n, 0)
})

test('indexFile and removeFile work per file for the watcher', async () => {
  const repo = tempRepo({ 'a.ts': 'export function a() {}\n' })
  const db = openStore(':memory:')
  const chunker = createFakeChunker()
  assert.equal(await indexFile(db, repo.root, 'a.ts', chunker), 'indexed')
  assert.equal(await indexFile(db, repo.root, 'a.ts', chunker), 'unchanged')
  repo.remove('a.ts')
  assert.equal(await indexFile(db, repo.root, 'a.ts', chunker), 'removed')
  assert.deepEqual(indexedPaths(db), [])
  repo.write('b.ts', 'export function b() {}\n')
  await indexFile(db, repo.root, 'b.ts', chunker)
  removeFile(db, 'b.ts')
  assert.deepEqual(indexedPaths(db), [])
  removeFile(db, 'never-existed.ts')
})

test('fixture repo indexes quickly and fully', async () => {
  const db = openStore(':memory:')
  const stats = await indexWorkspace(db, FIXTURE_REPO, createFakeChunker())
  console.log(`fixture index: ${JSON.stringify({ ...stats, ms: Math.round(stats.ms * 10) / 10 })}`)
  assert.ok(stats.filesIndexed >= 8)
  assert.ok(stats.ms < 2000)
})
