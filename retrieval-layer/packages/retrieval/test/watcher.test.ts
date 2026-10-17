import assert from 'node:assert/strict'
import { mkdirSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { afterEach, test } from 'node:test'
import type { DatabaseSync } from 'node:sqlite'
import { startPeriodicRescan, watchWorkspace } from '../src/watcher.ts'
import type { WorkspaceWatcher } from '../src/watcher.ts'
import { indexWorkspace } from '../src/indexer.ts'
import { search } from '../src/search.ts'
import { openStore } from '../src/store.ts'
import type { Chunker } from '../src/types.ts'
import { createFakeChunker, createTempRepo } from './helpers.ts'
import type { TempRepo } from './helpers.ts'

const DEBOUNCE_MS = 40
const WAIT_CAP_MS = 5000

const cleanups: (() => void | Promise<void>)[] = []
afterEach(async () => {
  while (cleanups.length) await cleanups.pop()!()
})

async function waitFor(condition: () => boolean, what: string): Promise<void> {
  const deadline = Date.now() + WAIT_CAP_MS
  while (!condition()) {
    if (Date.now() > deadline) assert.fail(`timed out waiting for ${what}`)
    await sleep(10)
  }
}

// Records every statement that could change data, so tests can assert "no DB writes".
function withWriteLog(db: DatabaseSync): { db: DatabaseSync; writes: string[] } {
  const writes: string[] = []
  const isWrite = (sql: string) => /^\s*(begin|insert|delete|update)/i.test(sql)
  const proxy = new Proxy(db, {
    get(target, prop) {
      const value = Reflect.get(target, prop, target)
      if (typeof value !== 'function') return value
      if (prop === 'exec' || prop === 'prepare') {
        return (sql: string, ...rest: unknown[]) => {
          if (isWrite(sql)) writes.push(sql.trim().slice(0, 40))
          return (value as Function).call(target, sql, ...rest)
        }
      }
      return value.bind(target)
    },
  })
  return { db: proxy, writes }
}

async function setup(files: Record<string, string> = {}, chunker: Chunker = createFakeChunker()) {
  const repo: TempRepo = createTempRepo(files)
  const real = openStore(':memory:')
  await indexWorkspace(real, repo.root, chunker)
  const log = withWriteLog(real)
  const errors: unknown[] = []
  const indexed: { path: string; result: string }[] = []
  const watcher: WorkspaceWatcher = watchWorkspace(log.db, repo.root, chunker, {
    debounceMs: DEBOUNCE_MS,
    onIndexed: (event) => indexed.push(event),
    onError: (error) => errors.push(error),
  })
  cleanups.push(async () => {
    await watcher.close()
    repo.cleanup()
  })
  const hasPath = (path: string) => real.prepare('select 1 from indexed_files where path = ?').get(path) !== undefined
  // macOS FSEvents needs a moment after fs.watch() before it reports changes; poke until the first event lands.
  const probe = 'zz-ready-probe.ts'
  const deadline = Date.now() + WAIT_CAP_MS
  while (!hasPath(probe)) {
    if (Date.now() > deadline) assert.fail('watcher never became ready')
    repo.write(probe, 'export function probe() {}\n')
    await sleep(50)
  }
  repo.remove(probe)
  await waitFor(() => !hasPath(probe), 'probe removal')
  await watcher.idle()
  indexed.length = 0
  const chunkCount = (path: string) => (real.prepare('select count(*) as n from chunks where path = ?').get(path) as { n: number }).n
  return { repo, db: real, writes: log.writes, watcher, errors, indexed, hasPath, chunkCount }
}

test('requestRescan repairs an index that missed a change, on the watcher queue', async () => {
  const t = await setup({ 'a.ts': 'export function alpha() {}\n' })
  // Simulates a dropped fs.watch event: the store forgets a.ts while the file is unchanged on disk.
  t.db.exec(`delete from chunks where path = 'a.ts'; delete from indexed_files where path = 'a.ts'`)
  t.watcher.requestRescan()
  await t.watcher.idle()
  assert.ok(t.hasPath('a.ts'))
  assert.ok(search(t.db, { query: 'alpha' }).length > 0)
})

test('editing a file makes new content searchable within 2s', async () => {
  const t = await setup({ 'a.ts': 'export function alpha() {}\n' })
  const started = Date.now()
  t.repo.write('a.ts', 'export function alpha() {}\nexport function pelicanFeeder() {}\n')
  await waitFor(() => search(t.db, { query: 'pelicanFeeder' }).length > 0, 'edited content in search')
  assert.ok(Date.now() - started < 2000)
})

test('creating a file in a new subdirectory indexes it', async () => {
  const t = await setup()
  t.repo.write('deep/er/new.ts', 'export function newcomer() {}\n')
  await waitFor(() => search(t.db, { query: 'newcomer' }).length > 0, 'new file in search')
  assert.deepEqual(search(t.db, { query: 'newcomer' }).map((h) => h.path), ['deep/er/new.ts'])
})

test('deleting a file removes its chunks', async () => {
  const t = await setup({ 'a.ts': 'export function doomed() {}\n' })
  assert.equal(search(t.db, { query: 'doomed' }).length, 1)
  t.repo.remove('a.ts')
  await waitFor(() => !t.hasPath('a.ts'), 'file removal')
  assert.equal(t.chunkCount('a.ts'), 0)
  assert.equal(search(t.db, { query: 'doomed' }).length, 0)
})

test('renaming a file removes the old path and indexes the new one', async () => {
  const t = await setup({ 'old.ts': 'export function traveller() {}\n' })
  renameSync(join(t.repo.root, 'old.ts'), join(t.repo.root, 'new.ts'))
  await waitFor(() => t.hasPath('new.ts') && !t.hasPath('old.ts'), 'rename')
  assert.deepEqual(search(t.db, { query: 'traveller' }).map((h) => h.path), ['new.ts'])
})

test('renaming a directory moves all its files', async () => {
  const t = await setup({ 'pkg/a.ts': 'export function inside() {}\n' })
  renameSync(join(t.repo.root, 'pkg'), join(t.repo.root, 'lib'))
  await waitFor(() => t.hasPath('lib/a.ts') && !t.hasPath('pkg/a.ts'), 'directory rename')
})

test('edits to gitignored, node_modules and .git paths cause zero DB writes', async () => {
  const t = await setup({ '.gitignore': 'secret.ts\nbuild/\n', 'a.ts': 'export function a() {}\n' })
  await t.watcher.idle()
  const before = t.writes.length
  t.repo.write('secret.ts', 'export function hidden() {}\n')
  t.repo.write('build/out.ts', 'export function built() {}\n')
  t.repo.write('node_modules/dep/index.ts', 'export function dep() {}\n')
  t.repo.write('.git/config', 'x')
  await sleep(DEBOUNCE_MS * 6)
  await t.watcher.idle()
  assert.equal(t.writes.length, before, `unexpected writes: ${t.writes.slice(before).join(' | ')}`)
  // Prove the watcher is alive: a normal file after the ignored ones still gets indexed.
  t.repo.write('b.ts', 'export function visible() {}\n')
  await waitFor(() => t.hasPath('b.ts'), 'sentinel file')
  assert.ok(!t.hasPath('secret.ts') && !t.hasPath('build/out.ts'))
  const ignored = ['secret.ts', 'build/out.ts', 'node_modules/dep/index.ts', '.git/config']
  assert.deepEqual(t.indexed.filter((e) => ignored.includes(e.path)), [])
})

test('adding a pattern to .gitignore removes that file chunks', async () => {
  const t = await setup({ 'keep.ts': 'export function keep() {}\n', 'drop.ts': 'export function drop() {}\n' })
  assert.ok(t.hasPath('drop.ts'))
  t.repo.write('.gitignore', 'drop.ts\n')
  await waitFor(() => !t.hasPath('drop.ts'), 'gitignored file removal')
  assert.equal(t.chunkCount('drop.ts'), 0)
  assert.ok(t.hasPath('keep.ts'))
})

test('a burst of 50 writes is indexed few times and never concurrently', async () => {
  const fake = createFakeChunker()
  let inFlight = 0
  let maxInFlight = 0
  const slow: Chunker = {
    async chunkFile(path, content) {
      inFlight++
      maxInFlight = Math.max(maxInFlight, inFlight)
      await sleep(15)
      const result = await fake.chunkFile(path, content)
      inFlight--
      return result
    },
  }
  const t = await setup({ 'a.ts': 'export function v0() {}\n' }, slow)
  const callsBefore = fake.calls.length
  for (let i = 1; i <= 50; i++) writeFileSync(join(t.repo.root, 'a.ts'), `export function burstVersion${i}() {}\n`)
  await waitFor(() => search(t.db, { query: 'burstVersion50' }).length > 0, 'last burst write')
  await t.watcher.idle()
  const calls = fake.calls.length - callsBefore
  assert.ok(calls >= 1 && calls <= 5, `expected few index runs, got ${calls}`)
  assert.equal(maxInFlight, 1)
})

test('close() waits for in-flight indexing to finish', async () => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => (release = resolve))
  let started = false
  const fake = createFakeChunker()
  const gated: Chunker = {
    async chunkFile(path, content) {
      if (path === 'late.ts') {
        started = true
        await gate
      }
      return fake.chunkFile(path, content)
    },
  }
  const t = await setup({}, gated)
  t.repo.write('late.ts', 'export function lateBloomer() {}\n')
  await waitFor(() => started, 'indexing to start')
  let closed = false
  const closing = t.watcher.close().then(() => (closed = true))
  await sleep(50)
  assert.equal(closed, false, 'close must wait for the queue')
  release()
  await closing
  assert.ok(t.hasPath('late.ts'))
})

test('indexing errors go to onError and watching continues', async () => {
  const fake = createFakeChunker()
  const flaky: Chunker = {
    async chunkFile(path, content) {
      if (path === 'bad.ts') throw new Error('boom')
      return fake.chunkFile(path, content)
    },
  }
  const t = await setup({}, flaky)
  t.repo.write('bad.ts', 'export function bad() {}\n')
  await waitFor(() => t.errors.length > 0, 'onError')
  assert.match((t.errors[0] as Error).message, /boom/)
  t.repo.write('good.ts', 'export function good() {}\n')
  await waitFor(() => t.hasPath('good.ts'), 'indexing after an error')
})

test('startPeriodicRescan picks up changes made without a watcher, and stops on close', async () => {
  const repo = createTempRepo()
  const db = openStore(':memory:')
  const rescan = startPeriodicRescan(db, repo.root, createFakeChunker(), 30)
  cleanups.push(async () => {
    await rescan.close()
    repo.cleanup()
  })
  mkdirSync(join(repo.root, 'x'))
  writeFileSync(join(repo.root, 'x', 'missed.ts'), 'export function missedEvent() {}\n')
  await waitFor(() => search(db, { query: 'missedEvent' }).length > 0, 'periodic rescan')
  await rescan.close()
  writeFileSync(join(repo.root, 'x', 'after.ts'), 'export function afterClose() {}\n')
  await sleep(120)
  assert.equal(search(db, { query: 'afterClose' }).length, 0)
})
