import { createRequire } from 'node:module'
import { dirname } from 'node:path'
import { Language, Parser } from 'web-tree-sitter'
import type { Node } from 'web-tree-sitter'
import { MAX_CHUNK_LINES, WINDOW_LINES, WINDOW_OVERLAP } from './types.ts'
import type { Chunk, ChunkKind, Chunker, SymbolKind } from './types.ts'

export type GrammarLang = 'typescript' | 'tsx' | 'javascript' | 'python' | 'rust' | 'go'
// 'runtime' is web-tree-sitter's own web-tree-sitter.wasm; the others are tree-sitter-<lang>.wasm.
export type WasmLang = GrammarLang | 'runtime'

export interface ChunkerOptions {
  // Returns the directory that holds the wasm for `lang`.
  wasmDir?: (lang: WasmLang) => string
}

const LANG_BY_EXT: Record<string, GrammarLang> = {
  '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript',
  '.tsx': 'tsx',
  '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.jsx': 'javascript',
  '.py': 'python',
  '.rs': 'rust',
  '.go': 'go',
}
const DOC_EXTS = new Set(['.md', '.mdx', '.txt', '.rst'])

const GRAMMAR_PACKAGE: Record<WasmLang, string> = {
  typescript: 'tree-sitter-typescript',
  tsx: 'tree-sitter-typescript',
  javascript: 'tree-sitter-javascript',
  python: 'tree-sitter-python',
  rust: 'tree-sitter-rust',
  go: 'tree-sitter-go',
  runtime: 'web-tree-sitter',
}

interface Sym {
  symbol: string
  kind: SymbolKind
  startRow: number
  endRow: number
}

function defaultWasmDir(lang: WasmLang): string {
  const require = createRequire(import.meta.url)
  // web-tree-sitter's exports map hides package.json, but its main entry sits next to the runtime wasm.
  const entry = lang === 'runtime' ? 'web-tree-sitter' : `${GRAMMAR_PACKAGE[lang]}/package.json`
  return dirname(require.resolve(entry))
}

function extOf(path: string): string {
  const base = path.slice(Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')) + 1)
  const dot = base.lastIndexOf('.')
  return dot <= 0 ? '' : base.slice(dot).toLowerCase()
}

function toLines(content: string): string[] {
  if (content === '') return []
  const lines = content.replace(/\r\n/g, '\n').split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

const isBlank = (line: string) => line.trim() === ''

// Rows are 0-based inclusive. A node ending at column 0 does not really include that row.
function span(node: Node): { startRow: number; endRow: number } {
  const startRow = node.startPosition.row
  let endRow = node.endPosition.row
  if (node.endPosition.column === 0 && endRow > startRow) endRow--
  return { startRow, endRow }
}

function sym(node: Node, symbol: string, kind: SymbolKind, outer: Node = node): Sym {
  return { symbol, kind, ...span(outer) }
}

function slidingWindows(lines: string[], fromRow: number, toRow: number): Chunk[] {
  const out: Chunk[] = []
  const step = WINDOW_LINES - WINDOW_OVERLAP
  for (let start = fromRow; start <= toRow; start += step) {
    const end = Math.min(start + WINDOW_LINES - 1, toRow)
    out.push({ symbol: null, symbolKind: null, startLine: start + 1, endLine: end + 1, text: lines.slice(start, end + 1).join('\n') })
    if (end === toRow) break
  }
  return out
}

// --- per-language symbol extraction -------------------------------------------------------

const JS_KINDS: Record<string, SymbolKind> = {
  function_declaration: 'fn',
  generator_function_declaration: 'fn',
  class_declaration: 'class',
  abstract_class_declaration: 'class',
  interface_declaration: 'type',
  type_alias_declaration: 'type',
  enum_declaration: 'type',
}
const FN_VALUES = new Set(['arrow_function', 'function_expression', 'function', 'generator_function'])

function jsSymbols(root: Node): Sym[] {
  const out: Sym[] = []
  for (const top of root.namedChildren) {
    const exported = top.type === 'export_statement'
    const node = exported ? (top.childForFieldName('declaration') ?? top) : top
    const kind = JS_KINDS[node.type]
    if (kind) {
      const name = node.childForFieldName('name')?.text ?? 'default'
      out.push(sym(node, name, kind, top))
      if (kind === 'class') {
        for (const m of node.childForFieldName('body')?.namedChildren ?? []) {
          const mName = m.childForFieldName('name')
          if (m.type === 'method_definition' && mName) out.push(sym(m, `${name}.${mName.text}`, 'method'))
        }
      }
    } else if (exported && node.type === 'lexical_declaration') {
      for (const d of node.namedChildren) {
        const value = d.childForFieldName('value')
        const name = d.childForFieldName('name')
        if (d.type === 'variable_declarator' && value && name && FN_VALUES.has(value.type)) out.push(sym(d, name.text, 'fn', top))
      }
    }
  }
  return out
}

function pySymbols(root: Node): Sym[] {
  const out: Sym[] = []
  for (const top of root.namedChildren) {
    const def = top.type === 'decorated_definition' ? top.childForFieldName('definition') : top
    const name = def?.childForFieldName('name')
    if (!def || !name) continue
    if (def.type === 'function_definition') out.push(sym(def, name.text, 'fn', top))
    else if (def.type === 'class_definition') {
      out.push(sym(def, name.text, 'class', top))
      for (const m of def.childForFieldName('body')?.namedChildren ?? []) {
        const md = m.type === 'decorated_definition' ? m.childForFieldName('definition') : m
        const mName = md?.childForFieldName('name')
        if (md?.type === 'function_definition' && mName) out.push(sym(md, `${name.text}.${mName.text}`, 'method', m))
      }
    }
  }
  return out
}

const RUST_TYPES = new Set(['struct_item', 'enum_item', 'trait_item', 'type_item', 'union_item'])

function rustSymbols(root: Node): Sym[] {
  const out: Sym[] = []
  for (const top of root.namedChildren) {
    const name = top.childForFieldName('name')
    if (top.type === 'function_item' && name) out.push(sym(top, name.text, 'fn'))
    else if (RUST_TYPES.has(top.type) && name) out.push(sym(top, name.text, 'type'))
    else if (top.type === 'impl_item') {
      const t = top.childForFieldName('type')
      const typeName = t?.type === 'generic_type' ? t.childForFieldName('type')?.text : t?.text
      if (!typeName) continue
      for (const m of top.childForFieldName('body')?.namedChildren ?? []) {
        const mName = m.childForFieldName('name')
        if (m.type === 'function_item' && mName) out.push(sym(m, `${typeName}.${mName.text}`, 'method'))
      }
    }
  }
  return out
}

function goReceiverType(method: Node): string | null {
  const text = method.childForFieldName('receiver')?.text ?? ''
  // "(s *Server[T])" or "(Server)" or "(_ Server)" -> Server
  const m = /([A-Za-z_][\w]*)\s*(?:\[[^\]]*\])?\s*\)\s*$/.exec(text)
  return m?.[1] ?? null
}

function goSymbols(root: Node): Sym[] {
  const out: Sym[] = []
  for (const top of root.namedChildren) {
    const name = top.childForFieldName('name')
    if (top.type === 'function_declaration' && name) out.push(sym(top, name.text, 'fn'))
    else if (top.type === 'method_declaration' && name) {
      const recv = goReceiverType(top)
      out.push(sym(top, recv ? `${recv}.${name.text}` : name.text, 'method'))
    } else if (top.type === 'type_declaration') {
      const specs = top.namedChildren.filter((c) => c.type === 'type_spec' || c.type === 'type_alias')
      for (const s of specs) {
        const sName = s.childForFieldName('name')
        if (sName) out.push(sym(s, sName.text, 'type', specs.length === 1 ? top : s))
      }
    }
  }
  return out
}

const EXTRACTORS: Record<GrammarLang, (root: Node) => Sym[]> = {
  typescript: jsSymbols, tsx: jsSymbols, javascript: jsSymbols, python: pySymbols, rust: rustSymbols, go: goSymbols,
}

// --- chunk assembly -----------------------------------------------------------------------

function symbolChunks(lines: string[], syms: Sym[]): Chunk[] {
  const out: Chunk[] = []
  for (const s of syms) {
    const size = s.endRow - s.startRow + 1
    const base = { symbol: s.symbol, symbolKind: s.kind }
    if (size <= MAX_CHUNK_LINES) {
      out.push({ ...base, startLine: s.startRow + 1, endLine: s.endRow + 1, text: lines.slice(s.startRow, s.endRow + 1).join('\n') })
    } else if (s.kind === 'class' || s.kind === 'type') {
      out.push({ ...base, startLine: s.startRow + 1, endLine: s.endRow + 1, text: lines.slice(s.startRow, s.startRow + MAX_CHUNK_LINES).join('\n') })
    } else {
      for (let start = s.startRow; start <= s.endRow; start += MAX_CHUNK_LINES) {
        const end = Math.min(start + MAX_CHUNK_LINES - 1, s.endRow)
        out.push({ ...base, startLine: start + 1, endLine: end + 1, text: lines.slice(start, end + 1).join('\n') })
      }
    }
  }
  return out
}

function uncoveredWindows(lines: string[], syms: Sym[]): Chunk[] {
  const covered = new Array<boolean>(lines.length).fill(false)
  for (const s of syms) for (let r = s.startRow; r <= s.endRow; r++) covered[r] = true
  const out: Chunk[] = []
  let row = 0
  while (row < lines.length) {
    if (covered[row]) { row++; continue }
    let end = row
    while (end + 1 < lines.length && !covered[end + 1]) end++
    let from = row
    let to = end
    while (from <= to && isBlank(lines[from]!)) from++
    while (to >= from && isBlank(lines[to]!)) to--
    if (from <= to) out.push(...slidingWindows(lines, from, to))
    row = end + 1
  }
  return out
}

function bySizeThenPosition(a: Chunk, b: Chunk): number {
  return a.startLine - b.startLine || (b.endLine - b.startLine) - (a.endLine - a.startLine)
}

function plainWindows(lines: string[]): Chunk[] {
  if (lines.every(isBlank)) return []
  return slidingWindows(lines, 0, lines.length - 1)
}

export function createChunker(opts: ChunkerOptions = {}): Chunker {
  const wasmDir = opts.wasmDir ?? defaultWasmDir
  let parserP: Promise<Parser> | null = null
  const langP = new Map<GrammarLang, Promise<Language>>()

  const getParser = () => (parserP ??= (async () => {
    const dir = wasmDir('runtime')
    await Parser.init({ locateFile: (name: string) => `${dir}/${name}` })
    return new Parser()
  })())

  const getLanguage = (lang: GrammarLang) => {
    let p = langP.get(lang)
    if (!p) {
      p = Language.load(`${wasmDir(lang)}/tree-sitter-${lang}.wasm`)
      langP.set(lang, p)
    }
    return p
  }

  async function chunkCode(lang: GrammarLang, lines: string[], content: string): Promise<Chunk[]> {
    // Language.load needs the runtime that Parser.init sets up, so it must not start before the parser resolves.
    const parser = await getParser()
    const language = await getLanguage(lang)
    // The parser is shared and setLanguage + parse are synchronous, so concurrent calls cannot interleave.
    parser.setLanguage(language)
    const tree = parser.parse(content)
    if (!tree) return plainWindows(lines)
    try {
      if (tree.rootNode.namedChildren.length === 0) return plainWindows(lines)
      const syms = EXTRACTORS[lang](tree.rootNode)
      return [...symbolChunks(lines, syms), ...uncoveredWindows(lines, syms)]
    } finally {
      tree.delete()
    }
  }

  return {
    async chunkFile(path: string, content: string): Promise<{ kind: ChunkKind; chunks: Chunk[] } | null> {
      const ext = extOf(path)
      const lines = toLines(content)
      if (lines.length === 0) return { kind: DOC_EXTS.has(ext) ? 'doc' : 'code', chunks: [] }
      if (DOC_EXTS.has(ext)) return { kind: 'doc', chunks: plainWindows(lines) }
      const lang = LANG_BY_EXT[ext]
      if (!lang) return { kind: 'code', chunks: plainWindows(lines) }
      // Tree-sitter columns count UTF-8 bytes/UTF-16 units but we only use rows, so the normalized text is safe to parse.
      const chunks = await chunkCode(lang, lines, lines.join('\n'))
      return { kind: 'code', chunks: chunks.sort(bySizeThenPosition) }
    },
  }
}
