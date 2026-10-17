const sqliteVec = require('sqlite-vec');

// Packaged: loadable path points inside app.asar; dlopen needs the real file in app.asar.unpacked.
function extPath() {
  return sqliteVec.getLoadablePath().replace(/app\.asar([\\/])/, 'app.asar.unpacked$1');
}

const SCHEMA = `
create table chunks (id integer primary key, path text not null, kind text not null, symbol text, symbol_kind text,
  start_line integer not null, end_line integer not null, text text not null, session_id text);
create index chunks_path on chunks(path);
create index chunks_symbol on chunks(symbol);
create virtual table chunks_fts using fts5(text, symbol, path, content='chunks', content_rowid='id', tokenize='unicode61 tokenchars ''_''');
create trigger chunks_ai after insert on chunks begin
  insert into chunks_fts(rowid, text, symbol, path) values (new.id, new.text, new.symbol, new.path);
end;
create trigger chunks_ad after delete on chunks begin
  insert into chunks_fts(chunks_fts, rowid, text, symbol, path) values ('delete', old.id, old.text, old.symbol, old.path);
end;
create trigger chunks_au after update on chunks begin
  insert into chunks_fts(chunks_fts, rowid, text, symbol, path) values ('delete', old.id, old.text, old.symbol, old.path);
  insert into chunks_fts(rowid, text, symbol, path) values (new.id, new.text, new.symbol, new.path);
end;`;

function fts(db) {
  db.exec(SCHEMA);
  const ins = db.prepare('insert into chunks(path,kind,symbol,symbol_kind,start_line,end_line,text) values (?,?,?,?,?,?,?)');
  db.exec('begin');
  for (let i = 0; i < 1000; i++)
    ins.run(`src/mod${i % 50}/file${i}.ts`, 'code', `handler_${i}`, 'fn', i, i + 10,
      `function handler_${i}(req) { ${i % 97 === 0 ? 'validate websocket auth_token here;' : ''} return db.query(${i}); }`);
  db.exec('commit');
  const rows = db.prepare(`select c.id, c.path, c.symbol, bm25(chunks_fts) as score from chunks_fts join chunks c on c.id = chunks_fts.rowid
    where chunks_fts match ? order by score limit 3`).all('websocket auth_token');
  const n = db.prepare('select count(*) n from chunks').get().n;
  if (n !== 1000 || rows.length !== 3) throw new Error(`bad fts result n=${n} rows=${rows.length}`);
  return rows.map((r) => ({ ...r }));
}

function vec(db) {
  db.loadExtension(extPath());
  const version = db.prepare('select vec_version() v').get().v;
  db.exec('create virtual table chunks_vec using vec0(chunk_id integer primary key, embedding float[384])');
  const ins = db.prepare('insert into chunks_vec(chunk_id, embedding) values (?, ?)');
  const rand = () => Buffer.from(new Float32Array(384).map(() => Math.random() - 0.5).buffer);
  db.exec('begin');
  for (let i = 1; i <= 1000; i++) ins.run(BigInt(i), rand());
  db.exec('commit');
  const rows = db.prepare('select chunk_id, distance from chunks_vec where embedding match ? order by distance limit 5').all(rand());
  if (rows.length !== 5) throw new Error('knn rows=' + rows.length);
  return { version, rows: rows.map((r) => ({ ...r, chunk_id: Number(r.chunk_id) })) };
}

function run(name, open) {
  const out = { driver: name };
  let db;
  try { db = open(); out.sqlite_version = db.prepare('select sqlite_version() v').get().v; }
  catch (e) { out.open = 'FAIL: ' + e.message; return out; }
  for (const [k, f] of [['fts5', fts], ['vec', vec]]) {
    try { out[k] = { pass: true, result: f(db) }; } catch (e) { out[k] = { pass: false, error: String(e.message) }; }
  }
  return out;
}

const results = [];
results.push(run('better-sqlite3', () => new (require('better-sqlite3'))(':memory:')));
results.push(run('node:sqlite', () => {
  const { DatabaseSync } = require('node:sqlite');
  return Object.assign(new DatabaseSync(':memory:', { allowExtension: true }), {});
}));
const summary = { node: process.versions.node, electron: process.versions.electron, extPath: extPath(), results };
if (process.parentPort) process.parentPort.postMessage(summary); else console.log(JSON.stringify(summary, null, 1));
