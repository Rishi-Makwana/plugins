"""The SQLite store: what it keeps, what it prunes, and how it degrades."""
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

import compress
from compress import store as store_mod
from compress.fetch import fetch
from conftest import load

SMALL = {"ok": False, "packageManager": "npm", "vulnerabilities": [
    {"id": "V-1", "packageName": "lodash", "version": "1.0.0", "severity": "critical",
     "cvssScore": 9.9, "title": "prototype pollution"},
    {"id": "V-2", "packageName": "axios", "version": "2.0.0", "severity": "low",
     "cvssScore": 2.0, "title": "minor thing"}]}


def fresh_db(tmp_path, name="compress.db"):
    return str(tmp_path / name)


def rows_of(db, render_id=None):
    conn = store_mod.connect(db)
    try:
        if render_id:
            return conn.execute('SELECT * FROM "rows" WHERE render_id = ?',
                                (render_id,)).fetchall()
        return conn.execute('SELECT * FROM "rows"').fetchall()
    finally:
        conn.close()


def test_store_writes_shown_and_hidden_rows(tmp_path):
    db = fresh_db(tmp_path)
    _, meta = compress.render(load("snyk-200.json"), preset="snyk", db=db,
                              max_bytes=4096)
    assert meta["stored"] is True
    conn = store_mod.connect(db)
    try:
        rows = conn.execute('SELECT shown, is_alert FROM "rows" WHERE render_id = ?',
                            (meta["render_id"],)).fetchall()
    finally:
        conn.close()
    assert len(rows) == meta["total"] == 200
    assert sum(1 for shown, _ in rows if shown) == meta["shown"]
    assert sum(1 for _, alert in rows if alert) == meta["alert_count"]


def test_data_is_stored_untruncated(tmp_path):
    db = fresh_db(tmp_path)
    text, meta = compress.render(load("deep-trunc.json"), preset="snyk", db=db)
    assert meta["fields_cut"]["title"] > 0
    conn = store_mod.connect(db)
    try:
        blob = conn.execute('SELECT data FROM "rows" WHERE render_id = ? LIMIT 1',
                            (meta["render_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert len(json.loads(blob)["title"]) == 900        # display cut at 240
    assert "…" not in json.loads(blob)["title"]


# 27 -- re-render is idempotent
def test_re_render_replaces_rather_than_duplicates(tmp_path):
    db = fresh_db(tmp_path)
    _, a = compress.render(SMALL, preset="snyk", db=db)
    _, b = compress.render(SMALL, preset="snyk", db=db)
    assert a["render_id"] == b["render_id"]
    conn = store_mod.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM "rows"').fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM rows_fts").fetchone()[0] == 2
    finally:
        conn.close()


# 28 -- GC keeps the newest 200 and leaves no orphans
def test_gc_keeps_two_hundred_renders_and_no_orphans(tmp_path):
    db = fresh_db(tmp_path)
    conn = store_mod.connect(db)
    try:
        now = time.time()
        for i in range(250):
            conn.execute("INSERT INTO renders (render_id, preset, detected, created_at,"
                         " raw_total, total, shown, meta) VALUES (?,?,?,?,?,?,?,?)",
                         (f"r{i:04d}", "snyk", 0, now - (250 - i), 1, 1, 1, "{}"))
            conn.execute('INSERT INTO "rows" (render_id, row_id, tier, tier_label,'
                         " is_alert, grp, score, shown, n, data)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (f"r{i:04d}", 0, 0, "critical", 1, "g", 1.0, 1, 1, '{"a":1}'))
        removed = store_mod.gc(conn, path=db)
        assert removed == 50
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 200
        orphans = conn.execute(
            'SELECT count(*) FROM "rows" WHERE render_id NOT IN'
            " (SELECT render_id FROM renders)").fetchone()[0]
        assert orphans == 0
    finally:
        conn.close()


def test_gc_also_drops_anything_older_than_keep_days(tmp_path):
    db = fresh_db(tmp_path)
    conn = store_mod.connect(db)
    try:
        old = time.time() - 30 * 86400
        for i in range(3):
            conn.execute("INSERT INTO renders (render_id, preset, detected, created_at,"
                         " raw_total, total, shown, meta) VALUES (?,?,?,?,?,?,?,?)",
                         (f"old{i}", "snyk", 0, old, 1, 1, 1, "{}"))
        assert store_mod.gc(conn, days=7, keep=200, path=db) == 3
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 0
    finally:
        conn.close()


# 29 -- no FTS5
def test_store_succeeds_without_fts5(tmp_path, monkeypatch):
    db = fresh_db(tmp_path, "nofts.db")
    real = store_mod._probe

    def no_fts(conn):
        real(conn)
        conn.fts5 = conn.contentless_delete = False

    monkeypatch.setattr(store_mod, "_probe", no_fts)
    _, meta = compress.render(SMALL, preset="snyk", db=db)
    assert meta["stored"] is True
    from compress.fetch import fetch
    text, fmeta = fetch(meta["render_id"], search="pollution", db=db)
    assert fmeta["search_mode"] == "like"
    assert fmeta["matched"] == 1


# 30 -- no contentless_delete: GC drops and rebuilds the index
def test_gc_rebuilds_the_index_without_contentless_delete(tmp_path, monkeypatch):
    db = fresh_db(tmp_path, "nocd.db")
    real = store_mod._probe

    def no_cd(conn):
        real(conn)
        conn.contentless_delete = False

    monkeypatch.setattr(store_mod, "_probe", no_cd)
    _, keep = compress.render(SMALL, preset="snyk", db=db)
    other = dict(SMALL)
    other["summary"] = "different payload"
    _, doomed = compress.render(other, preset="snyk", db=db)
    conn = store_mod.connect(db)
    try:
        assert conn.contentless_delete is False
        store_mod._delete_render(conn, [doomed["render_id"]])
        remaining = conn.execute('SELECT count(*) FROM "rows"').fetchone()[0]
        assert conn.execute("SELECT count(*) FROM rows_fts").fetchone()[0] == remaining
    finally:
        conn.close()


# 31 -- unwritable DB
def test_unwritable_db_leaves_the_render_intact(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        text, meta = compress.render(SMALL, preset="snyk", db=str(ro / "compress.db"))
        assert meta["stored"] is False
        assert meta["total"] == 2                 # the render itself still worked
        assert "fetch: unavailable (" in text
    finally:
        os.chmod(ro, 0o700)


# 32 -- corrupt DB is quarantined and recreated
def test_corrupt_db_is_quarantined(tmp_path):
    db = fresh_db(tmp_path, "corrupt.db")
    with open(db, "wb") as fh:
        fh.write(b"SQLite format 3\x00" + b"\xff" * 4096)
    _, meta = compress.render(SMALL, preset="snyk", db=db)
    assert meta["stored"] is True
    assert any(n.startswith("corrupt.db.corrupt-") for n in os.listdir(tmp_path))
    assert len(rows_of(db, meta["render_id"])) == 2


# 33 -- concurrent writers
def test_eight_concurrent_writers(tmp_path):
    db = fresh_db(tmp_path, "concurrent.db")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = tmp_path / "writer.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.path.join(root, 'vendor')!r})
        import compress
        worker = int(sys.argv[1])
        for i in range(50):
            compress.render({{"ok": False, "packageManager": "npm",
                             "vulnerabilities": [
                                 {{"id": f"W{{worker}}-{{i}}", "packageName": "p",
                                  "version": "1", "severity": "low"}}]}},
                            preset="snyk", db={db!r}, sink={str(tmp_path)!r})
    """))
    env = dict(os.environ, LDM_COMPRESS_KEEP_RENDERS="100000")
    procs = [subprocess.Popen([sys.executable, str(script), str(w)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for w in range(8)]
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err.decode()
        assert b"database is locked" not in err.lower()
    conn = store_mod.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 400
        assert conn.execute('SELECT count(*) FROM "rows"').fetchone()[0] == 400
    finally:
        conn.close()


# 34 -- row cap
def test_row_cap_keeps_the_top_tier(tmp_path, monkeypatch):
    db = fresh_db(tmp_path, "cap.db")
    monkeypatch.setenv("LDM_COMPRESS_MAX_ROWS", "5000")
    vulns = [{"id": f"V-{i}", "packageName": f"p{i % 50}", "version": "1",
              "severity": "critical" if i % 1200 == 0 else "low",
              "cvssScore": 9.9 if i % 1200 == 0 else 1.0}
             for i in range(12000)]
    text, meta = compress.render({"ok": False, "packageManager": "npm",
                                  "vulnerabilities": vulns}, preset="snyk", db=db)
    assert meta["rows_stored_truncated"] is True
    assert "rows stored: 5000 of 12000 (by tier)" in text
    conn = store_mod.connect(db)
    try:
        stored = conn.execute('SELECT count(*) FROM "rows" WHERE render_id = ?',
                              (meta["render_id"],)).fetchone()[0]
        tier0 = conn.execute('SELECT count(*) FROM "rows" WHERE render_id = ?'
                             " AND tier = 0", (meta["render_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert stored == 5000
    assert tier0 == meta["by_tier"]["critical"] == 10   # tier 0 fully present


def test_probe_reports_this_sqlite_build():
    conn = store_mod.connect(":memory:")
    try:
        assert conn.fts5 is True and conn.json1 is True
    finally:
        conn.close()


def test_store_never_raises_on_a_bad_record(tmp_path):
    db = fresh_db(tmp_path, "weird.db")
    records = [{"a": object(), "_m": {"full": {"a": object()}, "tier": 0,
                                      "label": "x", "alert": True, "grp": "g",
                                      "score": 1.0, "ident": None}}]
    meta = {"render_id": "deadbeef0000", "preset": "snyk", "detected": False,
            "raw_total": 1, "total": 1, "shown": 1}
    stored, notes, extra = store_mod.store(records, meta, {"name": "snyk"}, db=db)
    assert isinstance(stored, bool) and isinstance(notes, list)
    assert isinstance(extra, dict)


# Regression (D5, second audit): a lone UTF-16 surrogate is legal in a Python
# str (json.loads produces one from legal-but-malformed JSON) but sqlite3's
# text binding cannot encode it — and that used to fail the WHOLE render's
# storage, not just the one poisoned row.
def test_sqlite_text_strips_a_lone_surrogate():
    # str.encode(..., "replace") substitutes a literal "?" for an unencodable
    # character (U+FFFD is what the DECODE direction's "replace" produces).
    assert store_mod._sqlite_text("bad \ud800 here") == "bad ? here"
    assert store_mod._sqlite_text(None) is None
    assert store_mod._sqlite_text(5) == 5


def test_a_surrogate_in_one_field_does_not_fail_the_whole_batch(tmp_path):
    db = fresh_db(tmp_path, "surrogate.db")
    payload = json.loads(
        '{"packageManager":"npm","vulnerabilities":['
        '{"id":"CLEAN-1","packageName":"a","version":"1","severity":"critical"},'
        '{"id":"BAD-\\ud800-2","packageName":"b","version":"1","severity":"critical"},'
        '{"id":"CLEAN-3","packageName":"c","version":"1","severity":"low"}]}')
    text, meta = compress.render(payload, preset="snyk", db=db)
    assert meta["stored"] is True and meta["total"] == 3
    conn = store_mod.connect(db)
    try:
        n = conn.execute('SELECT count(*) FROM "rows" WHERE render_id = ?',
                         (meta["render_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert n == 3          # not 0 — the old bug lost the entire batch


def test_surrogate_in_the_group_key_or_identity_does_not_fail_storage(tmp_path):
    # tier_label/grp/ident are bound as raw strings, not JSON — a separate
    # sanitisation gap from the `data`/`meta` JSON columns.
    db = fresh_db(tmp_path, "surrogate2.db")
    payload = json.loads(
        '{"packageManager":"npm","vulnerabilities":'
        '[{"id":"X","packageName":"bad-\\ud800-pkg","version":"1","severity":"critical"}]}')
    text, meta = compress.render(payload, preset="snyk", db=db)
    assert meta["stored"] is True
    _, fm = fetch(meta["render_id"], db=db)
    assert fm["matched"] == 1


def test_db_shown_flags_match_the_footer_after_the_trim_loop(tmp_path):
    """store() runs after the final trim, so the DB cannot claim a row was
    shown that the footer withheld — `fetch --hidden` used to miss those."""
    db = fresh_db(tmp_path, "shown.db")
    vulns = [{"id": f"V-{i:04d}", "packageName": f"p{i % 12}", "version": "1.0.0",
              "severity": "critical" if i < 2 else "low",
              "cvssScore": 9.9 if i < 2 else 3.0,
              "title": "some finding title here"} for i in range(120)]
    _, meta = compress.render({"packageManager": "npm", "vulnerabilities": vulns},
                              preset="snyk", max_bytes=4096, db=db)
    conn = store_mod.connect(db)
    try:
        n = conn.execute('SELECT count(*) FROM "rows" WHERE render_id=? AND shown=1',
                         (meta["render_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert n == meta["shown"]
    _, fm = fetch(meta["render_id"], hidden=True, db=db, max_bytes=2048)
    assert fm["matched"] == meta["total"] - meta["shown"]


# D11: enumerating columns missed `preset` and `raw_path`; every bind tuple is
# sanitised at one choke point now, so no column can be forgotten.
def test_a_surrogate_in_the_preset_name_does_not_fail_storage(tmp_path):
    db = fresh_db(tmp_path, "preset.db")
    _, meta = compress.render(
        {"packageManager": "npm", "vulnerabilities": [
            {"id": "A", "packageName": "p", "version": "1", "severity": "critical"},
            {"id": "B", "packageName": "q", "version": "1", "severity": "low"}]},
        preset={"name": "my\ud800preset",
                "fields": ["id", "packageName", "version", "severity"],
                "rows_path": ["vulnerabilities"], "identity": ["id"],
                "rank": {"path": "severity", "order": ["critical", "low"],
                         "default": 1}},
        db=db)
    assert meta["stored"] is True and meta["total"] == 2


def test_a_surrogate_in_raw_path_does_not_fail_storage(tmp_path):
    db = fresh_db(tmp_path, "rawpath.db")
    recs = [{"id": "X", "_m": {"full": {"id": "X"}, "tier": 0, "label": "crit",
                               "alert": True, "grp": "g", "score": 1.0, "ident": "X"}}]
    meta = {"render_id": "abc123abc123", "preset": "snyk", "detected": False,
            "raw_total": 1, "total": 1, "shown": 1}
    stored, _notes, _extra = store_mod.store(recs, meta, {"name": "snyk"}, db=db,
                                             raw_path="/var/scan\udcff/x.json")
    assert stored is True


# D12: a stored row flagged is_alert=1 must carry data that explains the flag.
def test_no_stored_row_is_flagged_beyond_what_its_data_shows(tmp_path):
    db = fresh_db(tmp_path, "justify.db")
    _, meta = compress.render({"packageManager": "npm", "vulnerabilities": [
        {"id": "SNYK-JS-1", "packageName": "p", "version": "1", "severity": "high",
         "cvssScore": 7.0, "title": "prototype pollution (no known exploit)"},
        {"id": "SNYK-JS-1", "packageName": "p", "version": "1", "severity": "low",
         "cvssScore": 2.0, "exploitMaturity": "mature",
         "title": "MATURE EXPLOIT IN THE WILD"}]}, preset="snyk", db=db)
    conn = store_mod.connect(db)
    try:
        rows = conn.execute('SELECT is_alert, data FROM "rows" WHERE render_id=?',
                            (meta["render_id"],)).fetchall()
    finally:
        conn.close()
    assert rows
    for is_alert, data in rows:
        if is_alert:
            assert "mature" in data      # the flag is justified by its own row
