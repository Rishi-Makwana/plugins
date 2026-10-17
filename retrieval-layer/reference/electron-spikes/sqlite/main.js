const { app, utilityProcess } = require('electron');
const path = require('path');
app.whenReady().then(() => {
  const child = utilityProcess.fork(path.join(__dirname, 'worker.js'), [], { stdio: 'pipe' });
  let stderr = '', done = false;
  child.stderr.on('data', (d) => (stderr += d));
  const finish = (summary, code) => { if (done) return; done = true; console.log(JSON.stringify({ packaged: app.isPackaged, ...summary, stderr: stderr.slice(0, 2000) }, null, 1)); app.exit(code); };
  child.on('message', (m) => finish(m, m.results.every((r) => r.fts5?.pass && r.vec?.pass) ? 0 : 1));
  child.on('exit', (c) => finish({ error: 'worker exited ' + c }, 1));
  setTimeout(() => finish({ error: 'timeout' }, 1), 60000);
});
