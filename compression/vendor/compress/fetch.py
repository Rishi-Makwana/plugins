"""Read back what the slice withheld. Read-only; never raises.

A fetch can match 400 rows, so results go through reduce.py and footer.py
unchanged — retrieval must not become a second context bomb.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys

from . import footer as footer_mod
from . import store as store_mod
from .presets import PRESETS
from .reduce import reduce

SEP = "\x1f"
EXIT_OK, EXIT_NO_MATCH, EXIT_UNKNOWN, EXIT_ERROR = 0, 1, 2, 3
_OPS = {"gte": ">=", "lte": "<=", "eq": "=", "ne": "!=", "gt": ">", "lt": "<"}


def _jpath(key: str) -> str:
    return '$."' + key.replace('"', '""') + '"'


def _recent(conn, limit=10):
    return conn.execute(
        "SELECT render_id, preset, created_at, total, shown, source FROM renders"
        " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


def resolve_render(conn, render_id=None):
    """→ (row, error, candidates). Any unambiguous prefix works."""
    if render_id is None:
        rows = _recent(conn, 1)
        if not rows:
            return None, "no renders stored", []
        render_id = rows[0][0]
    hits = conn.execute(
        "SELECT render_id, preset, created_at, total, shown, source, meta, detected"
        " FROM renders WHERE render_id LIKE ? ORDER BY created_at DESC",
        (render_id + "%",)).fetchall()
    if not hits:
        return None, f"unknown render {render_id}", _recent(conn)
    if len(hits) > 1 and not any(h[0] == render_id for h in hits):
        return None, f"ambiguous prefix {render_id}", hits
    exact = next((h for h in hits if h[0] == render_id), hits[0])
    return exact, None, []


def _where_sql(where, json1):
    """→ (sql_fragments, params, keys). Values are never interpolated."""
    frags, params, keys = [], [], []
    for key, want in (where or {}).items():
        keys.append(key)
        if isinstance(want, dict):
            op = next((k for k in want if k in _OPS), None)
            if op is None:
                continue
            frags.append(f"CAST(json_extract(data, ?) AS REAL) {_OPS[op]} ?")
            params += [_jpath(key), float(want[op])]
        else:
            frags.append("lower(json_extract(data, ?)) = lower(?)")
            params += [_jpath(key), str(want)]
    if not json1:
        return [], [], keys          # caller falls back to a Python-side scan
    return frags, params, keys


def _match_python(row_data, where):
    for key, want in (where or {}).items():
        value = row_data.get(key)
        if isinstance(want, dict):
            op = next((k for k in want if k in _OPS), None)
            try:
                a, b = float(value), float(want[op])
            except (TypeError, ValueError):
                return False
            if not {"gte": a >= b, "lte": a <= b, "eq": a == b, "ne": a != b,
                    "gt": a > b, "lt": a < b}[op]:
                return False
        elif str(value).strip().lower() != str(want).strip().lower():
            return False
    return True


_BARE = re.compile(r'"[^"]*"|\S+')


def sanitise_query(text: str) -> str:
    """Quote every bare term so CVE-2024-1234 is not read as a NOT expression."""
    out = []
    for token in _BARE.findall(text or ""):
        if token.startswith('"') and token.endswith('"') and len(token) > 1:
            out.append(token)
        else:
            out.append('"' + token.replace('"', '""') + '"')
    return " ".join(out)


def _row_to_record(row, columns, preset, full):
    data = json.loads(row[columns["data"]] or "{}")
    limit = 0 if full else (preset.get("field_max_chars") or 240)
    disp = {}
    for key, value in data.items():
        if limit and isinstance(value, str) and len(value) > limit:
            value = value[:limit - 1] + "…"
        disp[key] = value
    if row[columns["n"]] and row[columns["n"]] > 1:
        disp["_n"] = row[columns["n"]]
    tier = row[columns["tier"]]
    score = row[columns["score"]] or 0.0
    desc = bool((preset.get("score") or {}).get("desc"))
    disp["_m"] = {"full": data, "tier": tier, "label": row[columns["tier_label"]],
                  "alert": bool(row[columns["is_alert"]]),
                  "alert_core": bool(row[columns["is_alert"]]),
                  "grp": row[columns["grp"]] or "", "score": score,
                  "ident": row[columns["ident"]], "index": row[columns["row_id"]],
                  "key": (tier, -score if desc else score, row[columns["row_id"]])}
    return disp


def _distribution(conn, render_id, key, json1):
    counts: dict[str, int] = {}
    if json1:
        cur = conn.execute(
            'SELECT json_extract(data, ?), count(*) FROM "rows" WHERE render_id = ?'
            " GROUP BY 1 ORDER BY 2 DESC", (_jpath(key), render_id))
        for value, n in cur.fetchall():
            if value is not None:
                counts[str(value)] = n
    else:
        for (blob,) in conn.execute('SELECT data FROM "rows" WHERE render_id = ?',
                                    (render_id,)):
            value = json.loads(blob or "{}").get(key)
            if value is not None:
                counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _keys_present(conn, render_id):
    keys: list[str] = []
    for (blob,) in conn.execute('SELECT data FROM "rows" WHERE render_id = ? LIMIT 50',
                                (render_id,)):
        for key in json.loads(blob or "{}"):
            if key not in keys:
                keys.append(key)
    return keys


def _describe(where, tier, alerts, hidden, group, search, ids):
    parts = []
    if ids:
        parts.append("ids " + ",".join(str(i) for i in ids))
    for key, want in (where or {}).items():
        parts.append(f"where {key}={want}" if not isinstance(want, dict)
                     else f"where {key}{want}")
    if tier is not None:
        parts.append("tier " + ",".join(str(t) for t in tier))
    if alerts:
        parts.append("alerts")
    if hidden:
        parts.append("hidden")
    if group:
        parts.append(f"group {group}")
    if search:
        parts.append(f'search "{search}"')
    return " ".join(parts) if parts else "summary"


def fetch(render_id=None, *, ids=None, where=None, tier=None, alerts=False,
          hidden=False, group=None, search=None, full=False, limit=None,
          max_bytes=None, db=None) -> tuple[str, dict]:
    conn = None
    try:
        conn = store_mod.connect(store_mod.db_path(db, None))
        return _fetch(conn, render_id, ids, where, tier, alerts, hidden, group,
                      search, full, limit, max_bytes)
    except Exception as exc:                       # read-only: never raise out
        return (f"compress_fetch: {type(exc).__name__}: {exc}\n",
                {"exit": EXIT_ERROR, "error": type(exc).__name__})
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _fetch(conn, render_id, ids, where, tier, alerts, hidden, group, search,
           full, limit, max_bytes):
    row, error, candidates = resolve_render(conn, render_id)
    if error:
        lines = [f"compress_fetch: {error}"]
        if candidates:
            lines.append("candidates, newest first:")
            lines += [f"  {c[0][:6]}  {c[1]:<12} {c[3]} records" for c in candidates]
        return "\n".join(lines) + "\n", {"exit": EXIT_UNKNOWN, "error": error}

    rid, preset_name, _created, source_total, source_shown, _source, meta_json, detected = row
    preset = PRESETS.get(preset_name) or {"name": preset_name, "fields": [],
                                          "rank": {}, "group_by": None}
    try:
        stored_meta = json.loads(meta_json)
    except ValueError:
        stored_meta = {}

    clauses = ['r.render_id = ?']
    params: list = [rid]
    if tier:
        clauses.append("r.tier IN (" + ",".join("?" * len(tier)) + ")")
        params += list(tier)
    if alerts:
        clauses.append("r.is_alert = 1")
    if hidden:
        clauses.append("r.shown = 0")
    if group:
        clauses.append("lower(r.grp) = lower(?)")
        params.append(group)
    if ids:
        parts, id_params = [], []
        for value in ids:
            parts += ["r.ident = ?", "r.ident LIKE ?"]
            id_params += [str(value), f"{value}{SEP}%"]
            if str(value).lstrip("-").isdigit():
                parts.append("r.row_id = ?")
                id_params.append(int(value))
        clauses.append("(" + " OR ".join(parts) + ")")
        params += id_params

    frags, wparams, where_keys = _where_sql(where, conn.json1)
    clauses += frags
    params += wparams

    search_mode = None
    if search:
        if conn.fts5:
            sql = ('SELECT r.* FROM "rows" r JOIN rows_fts f ON f.rowid = r.pk'
                   " WHERE " + " AND ".join(clauses) + " AND rows_fts MATCH ?"
                   " ORDER BY bm25(rows_fts)")
            try:
                cur = conn.execute(sql, params + [sanitise_query(search)])
                found = cur.fetchall()
                columns = [d[0] for d in cur.description]
                search_mode = "fts"
            except sqlite3.OperationalError:
                search_mode = None
        if search_mode is None:
            search_mode = "like"
            sql = ('SELECT r.* FROM "rows" r WHERE ' + " AND ".join(clauses)
                   + " AND r.data LIKE ? ORDER BY r.tier, -r.score, r.row_id")
            cur = conn.execute(sql, params + [f"%{search}%"])
            found = cur.fetchall()
            columns = [d[0] for d in cur.description]
    else:
        sql = ('SELECT r.* FROM "rows" r WHERE ' + " AND ".join(clauses)
               + " ORDER BY r.tier, -r.score, r.row_id")
        cur = conn.execute(sql, params)
        found = cur.fetchall()
        columns = [d[0] for d in cur.description]

    index = {name: i for i, name in enumerate(columns)}
    if where and not conn.json1:
        found = [r for r in found
                 if _match_python(json.loads(r[index["data"]] or "{}"), where)]
    if limit:
        found = found[:limit]

    query = _describe(where, tier, alerts, hidden, group, search, ids)
    asked = bool(ids or where or tier or alerts or hidden or group or search)

    notes = []
    if search_mode == "like":
        notes.append("search: FTS unavailable, matched with LIKE")

    if not asked:
        return _summary(rid, preset, preset_name, stored_meta, source_total,
                        source_shown, detected, notes)

    if not found:
        text = _zero_match(conn, rid, preset_name, query, source_total, where,
                           where_keys, notes)
        return text, {"exit": EXIT_NO_MATCH if asked else EXIT_OK,
                      "preset": preset_name, "render_id": rid, "total": 0,
                      "shown": 0, "withheld": 0, "matched": 0,
                      "source_total": source_total, "search_mode": search_mode,
                      "query": query, "mode": "fetch",
                      "by_tier": stored_meta.get("by_tier", {}),
                      "loss_reason": "empty"}

    records = [_row_to_record(r, index, preset, full) for r in found]
    if search_mode == "fts":
        for position, rec in enumerate(records):
            rec["_m"]["key"] = (0, position, rec["_m"]["index"])   # bm25 order

    budget = (max_bytes if max_bytes is not None
              else preset.get("max_bytes") or 12288)
    emitted, reduce_meta = reduce(records, preset, budget)
    ctx = dict(raw_total=len(records), bytes_in=stored_meta.get("bytes_in", 0),
               fields_cut={}, render_id=rid,
               persisted_path=stored_meta.get("full_data_path"),
               persist_error=None, detected=bool(detected),
               rank_field=(preset.get("rank") or {}).get("path"),
               kind=preset.get("kind", "records"), stored=True, notes=notes,
               extra={"mode": "fetch", "query": query, "source_total": source_total,
                      "source_shown": source_shown, "search_mode": search_mode,
                      "fetch_line": None})
    from . import _emit
    text, meta, _ = _emit(records, emitted, reduce_meta, preset, budget, ctx)
    meta["exit"] = EXIT_OK
    meta["matched"] = len(records)
    return text, meta


def _summary(rid, preset, preset_name, stored_meta, source_total, source_shown,
             detected, notes):
    """No predicate given: the tier table and the way back in, no rows."""
    by_tier = stored_meta.get("by_tier") or {}
    shown_by_tier = stored_meta.get("shown_by_tier") or {}
    meta = dict(stored_meta)
    meta.update(mode="fetch", query="summary", render_id=rid, preset=preset_name,
                source_total=source_total, source_shown=source_shown,
                total=source_total, shown=source_shown,
                withheld=source_total - source_shown, matched=source_total,
                detected=bool(detected), stored=True, notes=notes,
                fetch_line=None, exit=EXIT_OK, search_mode=None)
    coverage = {"tiers": [(label, shown_by_tier.get(label, 0), n)
                          for label, n in by_tier.items()],
                "groups": stored_meta.get("groups", 0),
                "groups_shown": stored_meta.get("groups_shown", 0)}
    return footer_mod.build(coverage, preset, meta), meta


def _zero_match(conn, rid, preset_name, query, source_total, where, where_keys, notes):
    """Zero match prints the real distribution — a typo must not cost a round trip."""
    lines = [footer_mod.RULE, f"{preset_name} · fetch {rid[:6]} · {query}",
             f"matched 0 of {source_total}"]
    keys = _keys_present(conn, rid)
    for key in where_keys or []:
        if key not in keys:
            lines.append(f"no field `{key}`; keys present: " + ", ".join(keys))
            continue
        counts = _distribution(conn, rid, key, conn.json1)
        if counts:
            shown = " · ".join(f"{v} {n}" for v, n in list(counts.items())[:12])
            lines.append(f"{key} values present: {shown}")
    if not where_keys:
        lines.append("fields present: " + ", ".join(keys))
    lines += notes
    lines.append(footer_mod.RULE)
    return "\n".join(lines) + "\n"


def _list_renders(conn, limit=20):
    rows = _recent(conn, limit)
    if not rows:
        return "compress_fetch: no renders stored\n"
    out = ["recent renders, newest first:"]
    for rid, preset, _created, total, shown, source in rows:
        out.append(f"  {rid[:6]}  {preset:<12} {shown:>5}/{total:<6} {source or ''}")
    return "\n".join(out) + "\n"


def _parse_where(values):
    where: dict = {}
    for item in values or []:
        for op, sym in (("gte", ">="), ("lte", "<="), ("eq", "=")):
            if sym in item:
                key, _, raw = item.partition(sym)
                if sym == "=":
                    where[key.strip()] = raw.strip()
                else:
                    where[key.strip()] = {op: float(raw.strip())}
                break
    return where


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="compress_fetch",
                                 description="retrieve rows a slice withheld")
    ap.add_argument("render_id", nargs="?")
    ap.add_argument("--ids", default=None, help="comma separated")
    ap.add_argument("--where", action="append", default=[], help="field=value")
    ap.add_argument("--tier", default=None, help="comma separated tier indexes")
    ap.add_argument("--group", default=None)
    ap.add_argument("--search", default=None)
    ap.add_argument("--alerts", action="store_true")
    ap.add_argument("--hidden", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-bytes", type=int, default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gc", action="store_true")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--keep", type=int, default=None)
    a = ap.parse_args(argv)

    if a.gc:
        try:
            path = store_mod.db_path(a.db, None)
            conn = store_mod.connect(path)
            removed = store_mod.gc(conn, days=a.days, keep=a.keep, path=path)
            conn.close()
            print(f"gc: {removed} renders removed")
            return EXIT_OK
        except Exception as exc:
            print(f"compress_fetch: {exc}", file=sys.stderr)
            return EXIT_ERROR

    if a.render_id is None and not any((a.ids, a.where, a.tier, a.group, a.search,
                                        a.alerts, a.hidden)):
        try:
            conn = store_mod.connect(store_mod.db_path(a.db, None))
            sys.stdout.write(_list_renders(conn))
            conn.close()
            return EXIT_OK
        except Exception as exc:
            print(f"compress_fetch: {exc}", file=sys.stderr)
            return EXIT_ERROR

    text, meta = fetch(a.render_id, ids=a.ids.split(",") if a.ids else None,
                       where=_parse_where(a.where),
                       tier=[int(t) for t in a.tier.split(",")] if a.tier else None,
                       alerts=a.alerts, hidden=a.hidden, group=a.group,
                       search=a.search, full=a.full, limit=a.limit,
                       max_bytes=a.max_bytes, db=a.db)
    if a.count:
        print(f"matched {meta.get('matched', meta.get('total', 0))}"
              f" of {meta.get('source_total', 0)}")
    elif a.json:
        print(json.dumps(meta, indent=2, default=str))
    else:
        sys.stdout.write(text)
    return int(meta.get("exit", EXIT_OK))


if __name__ == "__main__":
    sys.exit(main())
