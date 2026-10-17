const path = require('node:path')
const fs = require('node:fs')
const { Parser, Language } = require('web-tree-sitter')

const FN_VALUE = new Set(['arrow_function', 'function_expression', 'function'])
const KIND = {
  function_declaration: 'fn',
  generator_function_declaration: 'fn',
  class_declaration: 'class',
  abstract_class_declaration: 'class',
  method_definition: 'method',
  interface_declaration: 'type',
  type_alias_declaration: 'type',
}

function chunkFrom(node, symbol, kind) {
  return { symbol, symbol_kind: kind, start_line: node.startPosition.row + 1, end_line: node.endPosition.row + 1 }
}

function chunks(root) {
  const out = []
  for (const top of root.namedChildren) {
    const node = top.type === 'export_statement' ? top.childForFieldName('declaration') ?? top : top
    if (KIND[node.type]) {
      const name = node.childForFieldName('name')?.text ?? 'default'
      out.push(chunkFrom(top, name, KIND[node.type]))
      if (KIND[node.type] === 'class') {
        for (const m of node.childForFieldName('body').namedChildren) {
          if (m.type === 'method_definition') out.push(chunkFrom(m, `${name}.${m.childForFieldName('name').text}`, 'method'))
        }
      }
    } else if (node.type === 'lexical_declaration' && top.type === 'export_statement') {
      for (const d of node.namedChildren) {
        const v = d.childForFieldName('value')
        if (d.type === 'variable_declarator' && v && FN_VALUE.has(v.type)) out.push(chunkFrom(top, d.childForFieldName('name').text, 'fn'))
      }
    }
  }
  return out
}

process.parentPort.once('message', async ({ data: { treeSitterWasmDir, grammarWasm, samplePath } }) => {
  try {
    const t0 = performance.now()
    await Parser.init({ locateFile: (name) => path.join(treeSitterWasmDir, name) })
    const lang = await Language.load(grammarWasm)
    const parser = new Parser()
    parser.setLanguage(lang)
    const initMs = performance.now() - t0

    const src = fs.readFileSync(samplePath, 'utf8')
    const sampleChunks = chunks(parser.parse(src).rootNode)

    const big = Array(Math.ceil(5000 / src.split('\n').length)).fill(src).join('\n')
    const t1 = performance.now()
    const tree = parser.parse(big)
    const parseMs = performance.now() - t1
    const t2 = performance.now()
    const bigChunks = chunks(tree.rootNode)
    const chunkMs = performance.now() - t2

    process.parentPort.postMessage({
      ok: true, abi: lang.abiVersion, initMs, sampleLines: src.split('\n').length, sampleChunks,
      big: { lines: big.split('\n').length, parseMs, chunkMs, chunks: bigChunks.length, hasError: tree.rootNode.hasError },
    })
  } catch (e) {
    process.parentPort.postMessage({ ok: false, error: String(e), stack: e && e.stack })
  }
})
