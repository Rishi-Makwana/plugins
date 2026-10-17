import { DatabaseSync } from 'node:sqlite'

export const MIGRATIONS: string[] = [
  `
  create table indexed_files (
    path text primary key,
    hash text not null,
    mtime integer not null,
    indexed_at integer not null
  );

  create table chunks (
    id integer primary key,
    path text not null,
    kind text not null,
    symbol text,
    symbol_kind text,
    start_line integer not null,
    end_line integer not null,
    text text not null,
    session_id text,
    terms text not null default ''
  );
  create index chunks_path on chunks(path);
  create index chunks_symbol on chunks(symbol);

  create virtual table chunks_fts using fts5(
    text, symbol, path, terms,
    content='chunks', content_rowid='id',
    tokenize='unicode61 tokenchars ''_'''
  );

  create trigger chunks_ai after insert on chunks begin
    insert into chunks_fts(rowid, text, symbol, path, terms)
    values (new.id, new.text, new.symbol, new.path, new.terms);
  end;
  create trigger chunks_ad after delete on chunks begin
    insert into chunks_fts(chunks_fts, rowid, text, symbol, path, terms)
    values ('delete', old.id, old.text, old.symbol, old.path, old.terms);
  end;
  create trigger chunks_au after update on chunks begin
    insert into chunks_fts(chunks_fts, rowid, text, symbol, path, terms)
    values ('delete', old.id, old.text, old.symbol, old.path, old.terms);
    insert into chunks_fts(rowid, text, symbol, path, terms)
    values (new.id, new.text, new.symbol, new.path, new.terms);
  end;
  `,
  // Chat memory: nullable so existing code/doc rows are untouched. Chat turn index lives in start_line/end_line.
  `
  alter table chunks add column role text;
  alter table chunks add column created_at integer;
  create index chunks_session on chunks(session_id);
  `,
]

export function openStore(path: string | ':memory:'): DatabaseSync {
  // allowExtension only permits loadExtension(); nothing is loaded unless enableVectors is called.
  const db = new DatabaseSync(path, { allowExtension: true })
  if (path !== ':memory:') db.exec('PRAGMA journal_mode=WAL')
  db.exec('PRAGMA busy_timeout=5000')
  db.exec('PRAGMA foreign_keys=ON')
  migrate(db)
  return db
}

export function migrate(db: DatabaseSync): void {
  const row = db.prepare('PRAGMA user_version').get() as { user_version: number }
  for (let version = row.user_version + 1; version <= MIGRATIONS.length; version++) {
    db.exec('BEGIN')
    try {
      db.exec(MIGRATIONS[version - 1]!)
      db.exec(`PRAGMA user_version = ${version}`)
      db.exec('COMMIT')
    } catch (error) {
      db.exec('ROLLBACK')
      throw new Error(`retrieval store migration ${version} failed: ${(error as Error).message}`, { cause: error })
    }
  }
}

// Splits on camelCase, PascalCase, acronym, digit boundaries, `_` and `-`; lowercased and deduplicated.
const WORD_BOUNDARY = /(?<=\p{Ll})(?=\p{Lu})|(?<=\p{Lu})(?=\p{Lu}\p{Ll})|(?<=\p{L})(?=\p{N})|(?<=\p{N})(?=\p{L})|[_-]+/u

export function identifierTerms(text: string): string {
  const terms = new Set<string>()
  for (const token of text.match(/[\p{L}\p{N}_-]+/gu) ?? []) {
    for (const word of token.split(WORD_BOUNDARY)) {
      if (word) terms.add(word.toLowerCase())
    }
  }
  return [...terms].join(' ')
}
