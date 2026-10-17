export function runMigrations(db: { exec(sql: string): void }) {
  db.exec('create table if not exists users (id text primary key, name text not null)')
  db.exec('create table if not exists messages (id integer primary key, body text not null)')
}
