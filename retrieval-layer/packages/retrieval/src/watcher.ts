import { lstatSync, watch } from 'node:fs'
import { basename, dirname, join, sep } from 'node:path'
import type { DatabaseSync } from 'node:sqlite'
import type { IndexFileResult } from './indexer.ts'
import { indexFile, indexWorkspace, isPathIgnored, removeDirectory, removeFile } from './indexer.ts'
import type { Chunker } from './types.ts'

export const DEFAULT_DEBOUNCE_MS = 300

export interface WatchOptions {
  debounceMs?: number
  onIndexed?: (event: { path: string; result: IndexFileResult }) => void
  onError?: (error: unknown) => void
}

export interface WorkspaceWatcher {
  close(): Promise<void>
  idle(): Promise<void>
  // Queues a full rescan on the watcher's own serial queue; use this, not startPeriodicRescan, while watching.
  requestRescan(): void
}

// Known platform limits: fs.watch may drop or merge events, so pair this with startPeriodicRescan.
export function watchWorkspace(db: DatabaseSync, root: string, chunker: Chunker, opts: WatchOptions = {}): WorkspaceWatcher {
  const debounceMs = opts.debounceMs ?? DEFAULT_DEBOUNCE_MS
  const reportError = (error: unknown) => (opts.onError ?? (() => {}))(error)

  const timers = new Map<string, NodeJS.Timeout>()
  // Set keeps insertion order and coalesces repeats of a path that is already waiting.
  const queued = new Set<string>()
  let fullScanQueued = false
  let draining: Promise<void> | null = null
  let idleWaiters: (() => void)[] = []
  let closed = false

  const isBusy = () => timers.size > 0 || queued.size > 0 || fullScanQueued || draining !== null

  function settle(): void {
    if (isBusy()) return
    const waiters = idleWaiters
    idleWaiters = []
    for (const resolve of waiters) resolve()
  }

  async function processPath(rel: string): Promise<void> {
    let kind: 'file' | 'dir' | 'missing' = 'file'
    try {
      kind = lstatSync(join(root, rel)).isDirectory() ? 'dir' : 'file'
    } catch {
      kind = 'missing'
    }
    if (kind === 'dir') {
      // A new or moved-in directory: events for its children may have been lost, so rescan.
      await indexWorkspace(db, root, chunker)
      return
    }
    if (kind === 'missing') {
      // Platforms replay or invent events for paths we never indexed; those must not touch the DB.
      const prefix = `${rel}/`
      const known = db
        .prepare('select 1 from indexed_files where path = ? or substr(path, 1, ?) = ? limit 1')
        .get(rel, prefix.length, prefix)
      if (known === undefined) return
      removeDirectory(db, rel)
    }
    const result = await indexFile(db, root, rel, chunker)
    opts.onIndexed?.({ path: rel, result })
  }

  async function drain(): Promise<void> {
    while (fullScanQueued || queued.size > 0) {
      try {
        if (fullScanQueued) {
          fullScanQueued = false
          queued.clear()
          await indexWorkspace(db, root, chunker)
        } else {
          const rel = queued.values().next().value!
          queued.delete(rel)
          await processPath(rel)
        }
      } catch (error) {
        reportError(error)
      }
    }
  }

  function kick(): void {
    if (draining) return
    draining = drain().finally(() => {
      draining = null
      // Work may have been queued between the loop's last check and here.
      if (queued.size > 0 || fullScanQueued) kick()
      else settle()
    })
  }

  function enqueue(rel: string): void {
    timers.delete(rel)
    if (basename(rel) === '.gitignore') fullScanQueued = true
    else queued.add(rel)
    kick()
  }

  function onEvent(filename: string | Buffer | null): void {
    if (closed) return
    if (filename === null) {
      // Platform gave no path: the only safe answer is a rescan.
      fullScanQueued = true
      kick()
      return
    }
    const rel = filename.toString().split(sep).join('/')
    // A .gitignore changes the ignore set, so only its directory chain decides whether it matters.
    const subject = basename(rel) === '.gitignore' ? dirname(rel) : rel
    if (subject !== '.' && isPathIgnored(root, subject)) return
    clearTimeout(timers.get(rel))
    timers.set(rel, setTimeout(() => enqueue(rel), debounceMs))
  }

  const watcher = watch(root, { recursive: true }, (_event, filename) => {
    try {
      onEvent(filename)
    } catch (error) {
      reportError(error)
    }
  })
  watcher.on('error', reportError)

  const idle = () => (isBusy() ? new Promise<void>((resolve) => idleWaiters.push(resolve)) : Promise.resolve())

  return {
    idle,
    requestRescan() {
      if (closed) return
      fullScanQueued = true
      kick()
    },
    async close() {
      if (closed) return
      closed = true
      watcher.close()
      // Pending debounced edits are flushed rather than dropped, so close() never loses a change.
      for (const [rel, timer] of [...timers]) {
        clearTimeout(timer)
        enqueue(rel)
      }
      await idle()
    },
  }
}

// Standalone rescans for when no watcher runs; alongside a watcher use setInterval(watcher.requestRescan) so writes stay serial.
export function startPeriodicRescan(
  db: DatabaseSync,
  root: string,
  chunker: Chunker,
  intervalMs: number,
  onError?: (error: unknown) => void,
): { close(): Promise<void> } {
  let inFlight: Promise<unknown> | null = null
  const timer = setInterval(() => {
    if (inFlight) return
    inFlight = indexWorkspace(db, root, chunker)
      .catch((error) => onError?.(error))
      .finally(() => {
        inFlight = null
      })
  }, intervalMs)
  timer.unref()
  return {
    async close() {
      clearInterval(timer)
      await inFlight
    },
  }
}
