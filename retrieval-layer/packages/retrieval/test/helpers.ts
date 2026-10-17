import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Chunk, Chunker, ChunkKind } from '../src/types.ts'

export const FIXTURE_REPO = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'repo')

const DECLARATION =
  /^(?:export\s+)?(?:async\s+)?(?:(function)\s+(\w+)|(class)\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\()/

const DOC_EXTENSIONS = ['.md', '.txt']
const CODE_EXTENSIONS = ['.ts', '.js']

// One chunk per top-level function/class (declaration line to the line before the next one).
export function createFakeChunker(): Chunker & { calls: string[] } {
  const calls: string[] = []
  return {
    calls,
    async chunkFile(path, content) {
      calls.push(path)
      const extension = path.slice(path.lastIndexOf('.'))
      const lines = content.split('\n')
      if (DOC_EXTENSIONS.includes(extension)) {
        return { kind: 'doc' as ChunkKind, chunks: [wholeFile(lines)] }
      }
      if (!CODE_EXTENSIONS.includes(extension)) return null

      const starts: { index: number; symbol: string; symbolKind: 'fn' | 'class' }[] = []
      lines.forEach((line, index) => {
        const match = DECLARATION.exec(line)
        if (!match) return
        if (match[3]) starts.push({ index, symbol: match[4]!, symbolKind: 'class' })
        else starts.push({ index, symbol: (match[2] ?? match[5])!, symbolKind: 'fn' })
      })
      if (starts.length === 0) return { kind: 'code' as ChunkKind, chunks: [wholeFile(lines)] }

      const chunks: Chunk[] = starts.map((start, i) => {
        const endIndex = (starts[i + 1]?.index ?? lines.length) - 1
        const slice = lines.slice(start.index, endIndex + 1)
        while (slice.length > 1 && slice[slice.length - 1]!.trim() === '') slice.pop()
        return {
          symbol: start.symbol,
          symbolKind: start.symbolKind,
          startLine: start.index + 1,
          endLine: start.index + slice.length,
          text: slice.join('\n'),
        }
      })
      return { kind: 'code' as ChunkKind, chunks }
    },
  }
}

function wholeFile(lines: string[]): Chunk {
  return { symbol: null, symbolKind: null, startLine: 1, endLine: lines.length, text: lines.join('\n') }
}

export interface TempRepo {
  root: string
  write(relPath: string, content: string | Buffer): void
  remove(relPath: string): void
  cleanup(): void
}

export function createTempRepo(files: Record<string, string | Buffer> = {}): TempRepo {
  const root = mkdtempSync(join(tmpdir(), 'retrieval-test-'))
  const write = (relPath: string, content: string | Buffer) => {
    const abs = join(root, relPath)
    mkdirSync(dirname(abs), { recursive: true })
    writeFileSync(abs, content)
  }
  for (const [relPath, content] of Object.entries(files)) write(relPath, content)
  return {
    root,
    write,
    remove: (relPath) => rmSync(join(root, relPath), { recursive: true, force: true }),
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  }
}
