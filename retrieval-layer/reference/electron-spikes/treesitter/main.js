const { app, utilityProcess } = require('electron')
const path = require('node:path')

// Packaged: wasm shipped via extraResources into Contents/Resources/wasm. Dev: straight from node_modules.
const wasmDir = app.isPackaged
  ? path.join(process.resourcesPath, 'wasm')
  : null
const nm = path.join(__dirname, 'node_modules')
const treeSitterWasmDir = wasmDir ?? path.join(nm, 'web-tree-sitter')
const grammarWasm = wasmDir ? path.join(wasmDir, 'tree-sitter-typescript.wasm') : path.join(nm, 'tree-sitter-typescript', 'tree-sitter-typescript.wasm')

app.whenReady().then(() => {
  const child = utilityProcess.fork(path.join(__dirname, 'worker.js'))
  const finish = (res) => {
    console.log(JSON.stringify({ packaged: app.isPackaged, electron: process.versions.electron, treeSitterWasmDir, ...res }, null, 1))
    app.exit(res.ok ? 0 : 1)
  }
  child.once('message', finish)
  child.once('exit', (c) => finish({ ok: false, error: `worker exited ${c}` }))
  setTimeout(() => finish({ ok: false, error: 'timeout' }), 30000)
  child.postMessage({
    treeSitterWasmDir,
    grammarWasm,
    samplePath: path.join(__dirname, 'sample.ts'),
  })
})
