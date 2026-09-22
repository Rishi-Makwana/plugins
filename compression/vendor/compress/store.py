"""SQLite behind `fetch`: one row per record, shown and hidden alike.

Renders are immutable, so there are no sync triggers. Nothing here raises into
the renderer — every failure degrades to store=False and says so in the footer.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time

from .persist import resolve_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS renders (
    render_id  TEXT PRIMARY KEY,
    preset     TEXT NOT NULL,
    detected   INTEGER NOT NULL,
    created_at REAL NOT NULL,
    source     TEXT,
    raw_path   TEXT,
    raw_bytes  INTEGER,
    raw_total  INTEGER,
    total      INTEGER,
    shown      INTEGER,
    meta       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS "rows" (
    pk         INTEGER PRIMARY KEY,
    render_id  TEXT NOT NULL REFERENCES renders(render_id) ON DELETE CASCADE,
    row_id     INTEGER NOT NULL,
    tier       INTEGER NOT NULL,
    tier_label TEXT,
    is_alert   INTEGER NOT NULL,
    grp        TEXT,
    score      REAL,
    shown      INTEGER NOT NULL,
    ident      TEXT,
    n          INTEGER NOT NULL DEFAULT 1,
    data       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS rows_render ON "rows"(render_id);
CREATE INDEX IF NOT EXISTS rows_tier   ON "rows"(render_id, tier);
CREATE INDEX IF NOT EXISTS rows_alert  ON "rows"(render_id, is_alert);
CREATE INDEX IF NOT EXISTS rows_grp    ON "rows"(render_id, grp);
CREATE INDEX IF NOT EXISTS rows_shown  ON "rows"(render_id, shown);
"""

FTS_CONTENTLESS_DELETE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS rows_fts "
    "USING fts5(body, content='', contentless_delete=1)")
FTS_PLAIN = "CREATE VIRTUAL TABLE IF NOT EXISTS rows_fts USING fts5(body, content='')"

DEFAULT_MAX_ROWS = 50000
DEFAULT_KEEP_DAYS = 7
DEFAULT_KEEP_RENDERS = 200
VACUUM_ABOVE_BYTES = 256 * 1024 * 1024
GC_PROBABILITY = 0.05

_warned: set[str] = set()


def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def db_path(db=None, sink=None) -> str:
    return db or os.environ.get("LDM_COMPRESS_DB") \
        or os.path.join(resolve_dir(sink), "compress.db")


class Connection(sqlite3.Connection):
    """sqlite3.Connection has no __dict__, so the capability probe needs a subclass."""

    fts5 = contentless_delete = json1 = False


def _probe(conn: sqlite3.Connection) -> None:
    """FTS5, contentless_delete and JSON1, once per connection."""
    conn.fts5 = conn.contentless_delete = conn.json1 = False
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.probe_fts USING fts5(x)")
        conn.execute("DROP TABLE temp.probe_fts")
        conn.fts5 = True
    except sqlite3.Error:
        pass
    if conn.fts5:
        try:
            conn.execute("CREATE VIRTUAL TABLE temp.probe_cd "
                         "USING fts5(x, content='', contentless_delete=1)")
            conn.execute("DROP TABLE temp.probe_cd")
            conn.contentless_delete = True
        except sqlite3.Error:
            pass
    try:
        conn.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
        conn.json1 = True
    except sqlite3.Error:
        pass


def _quarantine(path: str) -> None:
    try:
        os.replace(path, f"{path}.corrupt-{int(time.time())}")
    except OSError:
        pass
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def connect(path: str, _retry: bool = True) -> sqlite3.Connection:
    """Open (creating if needed), probe, and quarantine a corrupt file once."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None,
                           factory=Connection)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous  = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        _probe(conn)
        conn.executescript(SCHEMA)
        if conn.fts5:
            conn.execute(FTS_CONTENTLESS_DELETE if conn.contentless_delete
                         else FTS_PLAIN)
        return conn
    except sqlite3.DatabaseError:
        conn.close()
        if not _retry:
            raise
        if path not in _warned:
            _warned.add(path)
            print(f"compress: {path} was unreadable; quarantined and recreated")
        _quarantine(path)
        return connect(path, _retry=False)


def _sqlite_text(s):
    """A lone UTF-16 surrogate is legal in a Python str — json.loads produces
    one from legal-but-malformed JSON text — but sqlite3's text binding cannot
    encode it, and raises UnicodeEncodeError, not sqlite3.Error, so it bypasses
    the transaction's own `except sqlite3.Error: ROLLBACK`. Strip it, once, at
    every place untrusted payload content reaches the DB as text (the `data`
    and `meta` JSON, and `tier_label`/`grp`/`ident`, all of which can carry a
    raw value straight from the record) — so one bad character in one field of
    one row can never fail the whole render's storage. Passes non-strings and
    None through unchanged; several call sites are nullable columns.
    """
    return s.encode("utf-8", "replace").decode("utf-8") if isinstance(s, str) else s


def _bind(params):
    """Sanitise a whole bind tuple. Enumerating individual columns is how
    `preset` and `raw_path` got missed — this cannot miss one by construction.
    """
    return tuple(_sqlite_text(p) for p in params)


def _fts_body(full: dict) -> str:
    return _sqlite_text(" ".join(str(v) for v in full.values() if v is not None))


def _row_tuples(records, render_id, max_rows):
    """(kept, total) — first max_rows by (tier, -score), original order preserved."""
    total = len(records)
    if total <= max_rows:
        return list(enumerate(records)), total
    ranked = sorted(enumerate(records),
                    key=lambda p: (p[1]["_m"]["tier"], -p[1]["_m"].get("score", 0.0), p[0]))
    kept = sorted(ranked[:max_rows], key=lambda p: p[0])
    return kept, total


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Drop and rebuild the index from "rows" — the no-contentless_delete path."""
    if not conn.fts5:
        return
    conn.execute("DROP TABLE IF EXISTS rows_fts")
    conn.execute(FTS_CONTENTLESS_DELETE if conn.contentless_delete else FTS_PLAIN)
    cur = conn.execute('SELECT pk, data FROM "rows"')
    batch = []
    for pk, data in cur.fetchall():
        try:
            full = json.loads(data)
        except ValueError:
            full = {}
        batch.append((pk, _fts_body(full)))
    if batch:
        conn.executemany("INSERT INTO rows_fts(rowid, body) VALUES (?, ?)", batch)


def _delete_render(conn: sqlite3.Connection, render_ids: list[str]) -> None:
    if not render_ids:
        return
    marks = ",".join("?" * len(render_ids))
    pks = [r[0] for r in conn.execute(
        f'SELECT pk FROM "rows" WHERE render_id IN ({marks})', render_ids).fetchall()]
    conn.execute(f"DELETE FROM renders WHERE render_id IN ({marks})", render_ids)
    conn.execute(f'DELETE FROM "rows" WHERE render_id IN ({marks})', render_ids)
    if conn.fts5 and pks:
        if conn.contentless_delete:
            conn.executemany("DELETE FROM rows_fts WHERE rowid = ?", [(p,) for p in pks])
        else:
            rebuild_fts(conn)


def gc(conn: sqlite3.Connection, days=None, keep=None, path=None) -> int:
    """Keep the newer of the last N days or the last N renders — whichever is smaller."""
    days = _env_int("LDM_COMPRESS_KEEP_DAYS", DEFAULT_KEEP_DAYS) if days is None else days
    keep = _env_int("LDM_COMPRESS_KEEP_RENDERS", DEFAULT_KEEP_RENDERS) if keep is None else keep
    cutoff = time.time() - days * 86400
    doomed = [r[0] for r in conn.execute(
        "SELECT render_id FROM renders WHERE render_id NOT IN "
        "(SELECT render_id FROM renders ORDER BY created_at DESC LIMIT ?) "
        "OR created_at < ?", (keep, cutoff)).fetchall()]
    if doomed:
        conn.execute("BEGIN")
        try:
            _delete_render(conn, doomed)
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
    if path and os.path.exists(path) and os.path.getsize(path) > VACUUM_ABOVE_BYTES:
        conn.execute("VACUUM")          # never inside a transaction
    return len(doomed)


def store(records, meta, preset, db=None, source=None, raw_path=None, raw_bytes=0):
    """Returns (stored, footer_notes, extra_meta). Never raises."""
    path = db_path(db, None)
    notes: list[str] = []
    extra: dict = {}
    conn = None
    try:
        conn = connect(path)
    except (sqlite3.Error, OSError) as exc:
        return False, [], {"fetch_line": f"fetch: unavailable ({_reason(exc)})"}

    try:
        max_rows = _env_int("LDM_COMPRESS_MAX_ROWS", DEFAULT_MAX_ROWS)
        kept, total = _row_tuples(records, meta["render_id"], max_rows)
        if len(kept) < total:
            extra["rows_stored_truncated"] = True
            notes.append(f"rows stored: {len(kept)} of {total} (by tier)")

        conn.execute("BEGIN IMMEDIATE")
        try:
            _delete_render(conn, [meta["render_id"]])   # re-render replaces
            conn.execute(
                "INSERT OR REPLACE INTO renders (render_id, preset, detected,"
                " created_at, source, raw_path, raw_bytes, raw_total, total, shown,"
                " meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                _bind((meta["render_id"], meta["preset"],
                       int(bool(meta.get("detected"))), time.time(), source,
                       raw_path, raw_bytes, meta["raw_total"], meta["total"],
                       meta["shown"],
                       json.dumps({**meta, "stored": True}, default=str))))

            start = conn.execute('SELECT COALESCE(MAX(pk), 0) FROM "rows"').fetchone()[0]
            row_batch, fts_batch = [], []
            for offset, (row_id, rec) in enumerate(kept):
                pk = start + offset + 1
                m = rec["_m"]
                full = m.get("full") or {}
                row_batch.append(_bind((
                    pk, meta["render_id"], row_id, m["tier"], m.get("label"),
                    int(bool(m.get("alert_core", m["alert"]))), m.get("grp"),
                    m.get("score", 0.0), int(bool(rec.get("shown"))),
                    m.get("ident"), int(rec.get("_n", 1)),
                    json.dumps(full, ensure_ascii=False, default=str))))
                fts_batch.append(_bind((pk, _fts_body(full))))
            conn.executemany(
                'INSERT INTO "rows" (pk, render_id, row_id, tier, tier_label,'
                ' is_alert, grp, score, shown, ident, n, data)'
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row_batch)
            if conn.fts5:
                conn.executemany("INSERT INTO rows_fts(rowid, body) VALUES (?, ?)",
                                 fts_batch)
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise

        if not conn.json1:
            notes.append("json1 unavailable: --where falls back to a row scan")
        if not conn.fts5:
            notes.append("fts5 unavailable: --search falls back to LIKE")
        if random.random() < GC_PROBABILITY:      # amortised
            try:
                gc(conn, path=path)
            except sqlite3.Error:
                pass
        return True, notes, extra
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        return False, [], {"fetch_line": f"fetch: unavailable ({_reason(exc)})"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _reason(exc) -> str:
    import errno
    code = getattr(exc, "errno", None)
    if code is not None:
        return errno.errorcode.get(code, str(code))
    return type(exc).__name__
