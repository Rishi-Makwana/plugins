import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { before, describe, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createChunker } from '../src/chunker.ts'
import { MAX_CHUNK_LINES, WINDOW_LINES, WINDOW_OVERLAP } from '../src/types.ts'
import type { Chunk, Chunker } from '../src/types.ts'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'chunker')
const fixture = (name: string) => readFileSync(join(FIXTURES, name), 'utf8')

let chunker: Chunker
before(() => {
  chunker = createChunker()
})

async function chunksOf(path: string, content: string): Promise<Chunk[]> {
  const result = await chunker.chunkFile(path, content)
  assert.ok(result, `expected ${path} to be supported`)
  return result.chunks
}

// Inline because tsc includes test/ and must not see a deliberately invalid .ts file.
const BROKEN_TS = 'export function ok() {\n  return 1\n}\n\nfunction broken( {\n  const = ;\n\nexport class Fine {\n  m() {}\n}\n'

const IMPORTS_TS = 'import a from "a"\nimport b from "b"\n'

const shape = (chunks: Chunk[]) => chunks.map((c) => ({ symbol: c.symbol, symbolKind: c.symbolKind, startLine: c.startLine, endLine: c.endLine }))
const w = (startLine: number, endLine: number) => ({ symbol: null, symbolKind: null, startLine, endLine })
const s = (symbol: string, symbolKind: 'fn' | 'class' | 'method' | 'type', startLine: number, endLine: number) => ({ symbol, symbolKind, startLine, endLine })

describe('symbol extraction per language', () => {
  test('typescript', async () => {
    assert.deepEqual(shape(await chunksOf('sample.ts', fixture('sample.ts'))), [
      w(1, 1),
      s('greet', 'fn', 3, 5),
      s('Greeter', 'class', 7, 14),
      s('Greeter.hello', 'method', 8, 10),
      s('Greeter.make', 'method', 11, 13),
      s('Shape', 'type', 16, 18),
      s('Id', 'type', 20, 20),
      s('Color', 'type', 22, 25),
      s('double', 'fn', 27, 27),
      s('fe', 'fn', 29, 31),
      w(33, 33),
    ])
  })

  test('tsx', async () => {
    assert.deepEqual(shape(await chunksOf('sample.tsx', fixture('sample.tsx'))), [w(1, 1), s('Button', 'fn', 3, 5), s('Card', 'fn', 7, 7)])
  })

  test('javascript', async () => {
    assert.deepEqual(shape(await chunksOf('sample.js', fixture('sample.js'))), [
      w(1, 1),
      s('load', 'fn', 3, 5),
      s('Repo', 'class', 7, 11),
      s('Repo.get', 'method', 8, 10),
      s('save', 'fn', 13, 13),
    ])
  })

  test('python includes decorators in range', async () => {
    assert.deepEqual(shape(await chunksOf('sample.py', fixture('sample.py'))), [
      w(1, 1),
      s('top', 'fn', 4, 5),
      s('decorated', 'fn', 8, 10),
      s('Foo', 'class', 13, 19),
      s('Foo.bar', 'method', 14, 15),
      s('Foo.baz', 'method', 17, 19),
    ])
  })

  test('rust: impl blocks are not chunks, their fns are Type.method', async () => {
    assert.deepEqual(shape(await chunksOf('sample.rs', fixture('sample.rs'))), [
      w(1, 1),
      s('run', 'fn', 3, 3),
      s('Point', 'type', 5, 7),
      s('Mode', 'type', 9, 11),
      s('Draw', 'type', 13, 15),
      s('Alias', 'type', 17, 17),
      w(19, 19),
      s('Point.new', 'method', 20, 22),
      w(23, 23),
    ])
  })

  test('go: pointer receivers are stripped, grouped types split per spec', async () => {
    assert.deepEqual(shape(await chunksOf('sample.go', fixture('sample.go'))), [
      w(1, 3),
      s('Hello', 'fn', 5, 7),
      s('Server', 'type', 9, 11),
      s('Server.Start', 'method', 13, 14),
      s('Server.Stop', 'method', 16, 17),
      w(19, 19),
      s('A', 'type', 20, 20),
      s('B', 'type', 21, 21),
      w(22, 22),
    ])
  })

  test('exported wrappers: chunk text starts at the export keyword', async () => {
    const chunks = await chunksOf('sample.ts', fixture('sample.ts'))
    assert.ok(chunks.find((c) => c.symbol === 'greet')!.text.startsWith('export function greet'))
    assert.ok(chunks.find((c) => c.symbol === 'Greeter')!.text.startsWith('export default class Greeter'))
    assert.equal(chunks.find((c) => c.symbol === 'double')!.text, 'export const double = (n: number) => n * 2')
  })
})

describe('edge cases', () => {
  test('empty file -> zero chunks', async () => {
    assert.deepEqual(await chunksOf('a.ts', ''), [])
    assert.deepEqual(await chunksOf('a.md', ''), [])
  })

  test('whitespace-only file -> zero chunks', async () => {
    assert.deepEqual(await chunksOf('a.ts', '\n  \n\t\n'), [])
    assert.deepEqual(await chunksOf('a.txt', '   \n\n'), [])
    assert.deepEqual(await chunksOf('a.json', '\n\n'), [])
  })

  test('only imports -> one window chunk, still searchable', async () => {
    const chunks = await chunksOf('imports.ts', IMPORTS_TS)
    assert.deepEqual(shape(chunks), [w(1, 2)])
    assert.equal(chunks[0]!.text, 'import a from "a"\nimport b from "b"')
  })

  test('CRLF is normalized to LF in chunk text', async () => {
    const chunks = await chunksOf('crlf.ts', fixture('crlf.ts'))
    assert.deepEqual(shape(chunks), [s('a', 'fn', 1, 3), s('b', 'fn', 5, 7)])
    assert.equal(chunks[0]!.text, 'export function a() {\n  return 1\n}')
    assert.ok(chunks.every((c) => !c.text.includes('\r')))
  })

  test('400-line function is split into consecutive windows keeping the symbol', async () => {
    const body = Array.from({ length: 398 }, (_, i) => `  const v${i} = ${i}`)
    const src = ['function huge() {', ...body, '}'].join('\n')
    const chunks = await chunksOf('big.ts', src)
    assert.deepEqual(shape(chunks), [
      s('huge', 'fn', 1, MAX_CHUNK_LINES),
      s('huge', 'fn', MAX_CHUNK_LINES + 1, 2 * MAX_CHUNK_LINES),
      s('huge', 'fn', 2 * MAX_CHUNK_LINES + 1, 400),
    ])
    assert.equal(chunks.map((c) => c.text).join('\n'), src)
  })

  test('300-line class: text truncated, range full, methods separate', async () => {
    const methods = Array.from({ length: 2 }, (_, m) => [`  m${m}() {`, ...Array.from({ length: 146 }, (_, i) => `    this.x = ${i}`), '  }'].flat()).flat()
    const src = ['class Big {', ...methods, '}'].join('\n')
    const lines = src.split('\n').length
    assert.equal(lines, 298)
    const chunks = await chunksOf('bigclass.ts', src)
    const cls = chunks[0]!
    assert.deepEqual([cls.symbol, cls.symbolKind, cls.startLine, cls.endLine], ['Big', 'class', 1, lines])
    assert.equal(cls.text.split('\n').length, MAX_CHUNK_LINES)
    assert.deepEqual(shape(chunks.slice(1)), [s('Big.m0', 'method', 2, 149), s('Big.m1', 'method', 150, 297)])
  })

  test('syntax error: still emits symbols and covers the broken region', async () => {
    const chunks = await chunksOf('broken.ts', BROKEN_TS)
    assert.deepEqual(shape(chunks), [s('ok', 'fn', 1, 3), w(5, 10)])
  })

  test('file the parser cannot make sense of at all falls back to windows', async () => {
    const chunks = await chunksOf('only-comment.ts', '// nothing here\n// at all\n')
    assert.deepEqual(shape(chunks), [w(1, 2)])
  })

  test('unicode identifiers', async () => {
    assert.deepEqual(shape(await chunksOf('unicode.ts', fixture('unicode.ts'))), [
      s('größe', 'fn', 1, 3),
      s('Ünïcode', 'class', 5, 9),
      s('Ünïcode.日本語', 'method', 6, 8),
    ])
  })

  test('markdown: sliding windows with overlap, short last window, kind doc', async () => {
    const lines = Array.from({ length: 130 }, (_, i) => `line ${i + 1}`)
    const result = await chunker.chunkFile('README.md', lines.join('\n'))
    assert.equal(result!.kind, 'doc')
    const step = WINDOW_LINES - WINDOW_OVERLAP
    assert.deepEqual(shape(result!.chunks), [w(1, WINDOW_LINES), w(1 + step, step + WINDOW_LINES), w(1 + 2 * step, 130)])
    assert.equal(result!.chunks[1]!.text.split('\n')[0], 'line 51')
  })

  test('unknown extension: kind code, windows only', async () => {
    const result = await chunker.chunkFile('config.yaml', 'a: 1\nb: 2\n')
    assert.equal(result!.kind, 'code')
    assert.deepEqual(shape(result!.chunks), [w(1, 2)])
  })

  test('injectable wasmDir is used for every grammar and the runtime', async () => {
    const asked: string[] = []
    const failing = createChunker({ wasmDir: (lang) => { asked.push(lang); return '/nonexistent' } })
    await assert.rejects(failing.chunkFile('a.ts', 'const x = 1'))
    assert.ok(asked.includes('runtime'))
  })
})
