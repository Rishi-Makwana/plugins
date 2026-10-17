import assert from 'node:assert/strict'
import { test } from 'node:test'
import { identifierTerms, migrate, openStore } from '../src/store.ts'

test('identifierTerms splits camel, Pascal, acronym, digit, underscore and hyphen boundaries', () => {
  assert.equal(identifierTerms('IndexService'), 'index service')
  assert.equal(identifierTerms('parse_HTTPResponse2'), 'parse http response 2')
  assert.equal(identifierTerms('kebab-case-name'), 'kebab case name')
  assert.equal(identifierTerms('getUserById'), 'get user by id')
})

test('identifierTerms deduplicates, lowercases and handles empty or symbol-only input', () => {
  assert.equal(identifierTerms('fooBar FooBar foo_bar'), 'foo bar')
  assert.equal(identifierTerms(''), '')
  assert.equal(identifierTerms('(){} -- ;;'), '')
})

test('identifierTerms handles unicode identifiers', () => {
  assert.equal(identifierTerms('naïveÜber'), 'naïve über')
})

test('openStore creates schema at user_version 1 and migrate is idempotent', () => {
  const db = openStore(':memory:')
  const version = () => (db.prepare('PRAGMA user_version').get() as { user_version: number }).user_version
  assert.equal(version(), 1)
  migrate(db)
  assert.equal(version(), 1)
  const tables = db.prepare("select name from sqlite_master where type in ('table','trigger')").all() as { name: string }[]
  const names = tables.map((row) => row.name)
  for (const expected of ['indexed_files', 'chunks', 'chunks_fts', 'chunks_ai', 'chunks_ad', 'chunks_au']) {
    assert.ok(names.includes(expected), `missing ${expected}`)
  }
})

test('fts index follows chunk insert, update and delete through the triggers', () => {
  const db = openStore(':memory:')
  const count = (term: string) =>
    (db.prepare('select count(*) as n from chunks_fts where chunks_fts match ?').get(`"${term}"`) as { n: number }).n
  db.prepare(
    "insert into chunks (path, kind, start_line, end_line, text) values ('a.ts', 'code', 1, 1, 'alpha beta')",
  ).run()
  assert.equal(count('alpha'), 1)
  db.prepare("update chunks set text = 'gamma' where path = 'a.ts'").run()
  assert.equal(count('alpha'), 0)
  assert.equal(count('gamma'), 1)
  db.prepare("delete from chunks where path = 'a.ts'").run()
  assert.equal(count('gamma'), 0)
})
