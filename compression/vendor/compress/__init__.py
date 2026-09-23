"""compress — turn large repetitive tool output into a small slice plus an exact
accounting of what was withheld.

    text, meta = render(payload, preset="snyk")

Compression may hide records. It may never hide their existence.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from . import footer as footer_mod
from . import store as store_mod
from .adapters.junit import junit_to_records
from .classify import ident_of, is_alert, sort_key, tier_of
from .dedupe import dedupe
from .paths import get
from .persist import persist
from .presets import PRESETS, sniff
from .reduce import (FOOTER_RESERVE, alert_header, body_line, bytelen,
                     group_header, group_key, reduce, truncate_bytes)

__all__ = ["render", "render_lines", "fetch", "sniff", "PRESETS", "PRESET_DEFAULTS"]

PRESET_DEFAULTS = {
    "kind": "records", "field_max_chars": 240, "identity": None, "score": None,
    "alert": [{"rank_tier": 0}], "group_by": None, "max_bytes": 12288,
}

# A payload no serialiser could read at all. Its render_id would not be a
# content hash, so it is never used as a persistence or storage key.
UNREPRESENTABLE = "<unrepresentable payload>"


def _type_name(obj) -> str:
    """type(x).__name__ can itself raise (a metaclass with a raising __name__)."""
    try:
        return type(obj).__name__
    except Exception:
        return "Exception"


def with_defaults(preset: dict) -> dict:
    out = dict(PRESET_DEFAULTS)
    out.update(preset or {})
    return out


def _safe_text(payload) -> str:
    """Best-effort text for a dict/list payload. Never raises.

    json.dumps can still fail three ways even with default=str: a circular
    reference (raises before default is consulted), a value whose __str__
    itself raises (default's own callback, uncaught by json), or — inside
    the crash handler — the very payload that made render() fail in the
    first place. This is the one function the crash path depends on, so
    each fallback must be strictly safer than the one before it.
    """
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        pass
    try:
        # NOT repr(): a value may redact in __str__ and leak in __repr__ (the
        # "friendly str, verbose repr" pattern around secrets), and json.dumps'
        # default=str would have honoured the redaction. Walk it by hand with
        # str() on leaves so the fallback never prints more than the normal
        # path would have.
        return json.dumps(_safe_walk(payload), ensure_ascii=False)
    except Exception:
        pass
    # Deliberately CONSTANT. render_id is defined as sha256(raw)[:12] — a
    # CONTENT hash — and a payload that reaches here has no readable content,
    # so no honest id exists for it. Perturbing the hash (id(payload), a clock)
    # only trades one bug for another: id() is reused the moment an object is
    # freed, so sequential renders collide and store()'s delete-then-insert
    # wipes an unrelated render; a clock breaks re-render idempotence instead.
    # The answer is not to invent an id but to refuse to key anything by it —
    # _render treats this marker as "do not persist, do not store".
    return UNREPRESENTABLE


def _safe_walk(obj, seen=None, depth=0):
    """A json.dumps-able copy that survives cycles, throwing values and depth.

    Leaves go through str(), never repr(), so a value's own redaction stands.
    """
    if depth > 60:
        return "<too deep>"
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return "<cycle>"
    seen = seen | {id(obj)}
    try:
        if isinstance(obj, dict):
            return {str(k): _safe_walk(v, seen, depth + 1) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_safe_walk(v, seen, depth + 1) for v in obj]
    except Exception:
        return f"<unreadable {type(obj).__name__}>"
    try:
        return str(obj)
    except Exception:
        return f"<unreadable {type(obj).__name__}>"


def _raw_bytes(payload) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8", "replace")
    # A dict/list can legally contain an unpaired UTF-16 surrogate (json.loads
    # accepts one; json.dumps happily re-serialises it into the Python str) that
    # UTF-8 cannot represent. "replace" degrades that one character rather than
    # raising — render() must never raise, including out of its own crash path.
    return _safe_text(payload).encode("utf-8", "replace")


def _as_text(payload) -> str:
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        return payload
    return _safe_text(payload)


def parse(payload):
    """→ (data, kind, fell_back). Order: dict/list · JSON · NDJSON · JUnit · lines."""
    if isinstance(payload, (dict, list)):
        return payload, "records", False
    text = _as_text(payload)
    stripped = text.strip()
    looked_structured = stripped[:1] in ("{", "[", "<")
    if stripped:
        if stripped[0] in "{[":
            try:
                return json.loads(text), "records", False
            except Exception:
                pass
            rows, ok = [], True
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    ok = False
                    break
            if ok and rows:
                return rows, "records", False
        if stripped[0] == "<" and "<testsuite" in text:
            records = junit_to_records(text)
            if records:
                return records, "junit", False
    return text, "lines", looked_structured


def locate(data, preset):
    """→ (records, path_used, empty_path, skipped). First rows_path yielding ≥1 wins.

    `skipped` counts elements that were not dicts. Dropping them silently would
    understate the input: counts must be exact over ALL records (rule 2).
    """
    empty_path = None
    for path in preset.get("rows_path") or ["$"]:
        value = get(data, path)
        if isinstance(value, list):
            rows = [r for r in value if isinstance(r, dict)]
            if rows:
                return rows, path, None, len(value) - len(rows)
            if empty_path is None:
                empty_path = path
    return [], None, empty_path, 0


def project(records, preset):
    """Projected (display) records plus the untruncated original under _m.full."""
    fields = preset["fields"]
    limit = preset.get("field_max_chars") or PRESET_DEFAULTS["field_max_chars"]
    cut: Counter = Counter()
    out = []
    for rec in records:
        full, disp = {}, {}
        for f in fields:
            value = get(rec, f)
            if value is None:
                continue
            full[f] = value
            if isinstance(value, str) and len(value) > limit:
                value = value[:limit - 1] + "…"
                cut[f] += 1
            disp[f] = value
        disp["_m"] = {"full": full}
        out.append(disp)
    return out, dict(cut)


def classify_all(records, preset):
    rank, score, alerts = preset.get("rank"), preset.get("score"), preset.get("alert")
    for i, rec in enumerate(records):
        tier, label = tier_of(rec, rank)
        alert = is_alert(rec, alerts, tier)
        meta = rec["_m"]
        meta.update(tier=tier, label=label, alert=alert, alert_core=alert, index=i,
                    key=sort_key(rec, tier, score, i), grp=group_key(rec, preset))
        raw = 0.0
        if score and score.get("path"):
            try:
                raw = float(get(rec, score["path"]) or 0)
            except (TypeError, ValueError):
                raw = 0.0
        meta["score"] = raw
        meta["ident"] = ident_of(rec, preset.get("identity"), get)


def build_body(emitted, records, reduce_meta):
    alerts = [r for r in emitted if r["_m"]["alert"]]
    alert_total = sum(1 for r in records if r["_m"]["alert"])
    out = []
    if alert_total:
        out.append(alert_header(len(alerts), alert_total))
        out += [body_line(r) for r in alerts]
    shown_by_group: dict[str, list] = {}
    for rec in emitted:
        if not rec["_m"]["alert"]:
            shown_by_group.setdefault(rec["_m"]["grp"], []).append(rec)
    totals = Counter(r["_m"]["grp"] for r in records if not r["_m"]["alert"])
    for key in reduce_meta.get("group_order", []):
        rows = shown_by_group.get(key)
        if not rows:
            continue
        out.append(group_header(key, totals[key], len(rows)))
        out += [body_line(r) for r in rows]
    return "\n".join(out) + ("\n" if out else "")


def _structure(data) -> str:
    return footer_mod.outline(data)


def _emit(records, emitted, reduce_meta, preset, max_bytes, ctx, body_fn=None):
    """Body + footer, trimming non-alert rows from the tail until it fits.

    Budgets are bytes, not characters, throughout (see reduce.bytelen).
    """
    body_fn = body_fn or build_body
    emitted = list(emitted)
    compact = False
    while True:
        body = body_fn(emitted, records, reduce_meta)
        reduce_meta["groups_shown"] = len({r["_m"]["grp"] for r in emitted
                                           if not r["_m"]["alert"]})
        coverage = footer_mod.coverage_of(records, preset, reduce_meta)
        meta = footer_mod.assemble_meta(preset, records, emitted, reduce_meta,
                                        bytes_out=bytelen(body), **ctx)
        text = body + footer_mod.build(coverage, preset, meta, compact=compact)
        meta = footer_mod.assemble_meta(preset, records, emitted, reduce_meta,
                                        bytes_out=bytelen(text), **ctx)
        text = body + footer_mod.build(coverage, preset, meta, compact=compact)
        meta["bytes_out"] = bytelen(text)
        if meta["bytes_out"] <= max_bytes:
            return text, meta, emitted
        droppable = [i for i, r in enumerate(emitted) if not r["_m"]["alert"]]
        if droppable:
            victim = emitted.pop(droppable[-1])
            victim["shown"] = False
            continue
        if not compact:
            # Only alerts left and still over: the footer itself is too big.
            # One line per tier is unbounded (a preset may declare 100 tiers),
            # so fall back to a compact tier table — every tier is still named,
            # which is what rule 3 actually requires.
            compact = True
            continue
        return text, meta, emitted   # irreducible: alerts are never dropped


def render(payload, preset=None, max_bytes=None, sink=None,
           store=True, source=None, db=None) -> tuple[str, dict]:
    ctx = {"persisted": None, "error": None}
    try:
        return _render(payload, preset, max_bytes, sink, store, source, db, ctx)
    except Exception as exc:  # fail open: the payload always comes back
        return _crash(payload, preset, exc, sink, ctx, max_bytes)


def _render(payload, preset, max_bytes, sink, store, source, db, ctx):
    raw = _raw_bytes(payload)
    data, kind, fell_back = parse(payload)

    detected = False
    if isinstance(preset, dict):
        cfg = with_defaults(preset)
    elif isinstance(preset, str):
        cfg = with_defaults(PRESETS.get(preset) or {"name": preset, "fields": []})
    elif kind == "junit":
        cfg = with_defaults(PRESETS["junit"])  # the adapter already decided
    else:
        name = sniff(data) if kind != "lines" else None
        cfg = with_defaults(PRESETS[name]) if name else None

    if kind == "lines" or (cfg and cfg.get("kind") == "lines"):
        from .adapters.lines import render_lines as _render_lines
        return _render_lines(_as_text(payload), preset=preset or "logs",
                             max_bytes=max_bytes, sink=sink, store=store,
                             source=source, db=db, fell_back=fell_back, raw=raw)

    if cfg is None:
        from .autodetect import detect
        cfg, detected = with_defaults(detect(data)), True

    render_id = hashlib.sha256(raw).hexdigest()[:12]
    # No readable content means no content hash, and a render_id that is not a
    # content hash must not key a file or a DB row: store() deletes-then-inserts
    # by it, so a shared id would wipe an unrelated render.
    unrepresentable = raw == UNREPRESENTABLE.encode("utf-8")
    if unrepresentable:
        path, error = None, "unrepresentable"
        store = False
    else:
        path, error = persist(raw, cfg.get("name", "raw"), sink)
    ctx.update(persisted=path, error=error)

    records, used_path, empty_path, skipped = locate(data, cfg)
    if not records and empty_path is None:
        # rows_path missed entirely: name the path we looked for, then outline the keys.
        empty_path = (cfg.get("rows_path") or ["$"])[0]

    projected, fields_cut = project(records, cfg)
    # CLASSIFY before DEDUPE: dedupe must see each candidate's tier/alert to pick
    # the most severe survivor of a collision, never a blindly-first one.
    classify_all(projected, cfg)
    deduped, raw_total = dedupe(projected, cfg.get("identity"))

    budget = (max_bytes if max_bytes is not None
              else cfg.get("max_bytes") or PRESET_DEFAULTS["max_bytes"])
    emitted, reduce_meta = reduce(deduped, cfg, budget)

    notes = []
    if fell_back:
        notes.append("could not parse as json/ndjson/junit; rendered as lines")
    if skipped:
        notes.append(f"skipped {skipped} non-record element(s) in `{used_path}`")
    if not deduped:
        reduce_meta["loss_reason"] = "empty"
    elif fell_back and reduce_meta["loss_reason"] == "lossless":
        reduce_meta["loss_reason"] = "parse_fallback"

    extra = {}
    if not deduped:
        extra = {"empty_path": empty_path or used_path, "structure": _structure(data)}

    common = dict(raw_total=raw_total, bytes_in=len(raw), fields_cut=fields_cut,
                  render_id=render_id, persisted_path=path, persist_error=error,
                  detected=detected, rank_field=(cfg.get("rank") or {}).get("path"),
                  kind=cfg.get("kind", "records"), stored=False, notes=notes,
                  extra=extra)

    # Render optimistically as if storage will succeed, so the footer already
    # carries the `fetch:` line and is its FINAL length. The trim loop can drop
    # further rows, and store() must see those final `shown` flags — storing
    # first left the DB claiming rows were shown that the footer had withheld,
    # so `fetch --hidden` missed them.
    if store and deduped:
        common["stored"] = True
    text, meta, emitted = _emit(deduped, emitted, reduce_meta, cfg, budget, common)

    # If the footer came out bigger than reduce reserved for it, the slice can
    # overrun with nothing droppable left (alerts are never dropped). Redo the
    # reduction once against the footer's MEASURED size rather than an estimate.
    if meta["bytes_out"] > budget and emitted:
        measured = bytelen(text[text.find(footer_mod.RULE):]) if footer_mod.RULE in text else 0
        if measured:
            emitted, reduce_meta = reduce(deduped, cfg, budget, reserve=measured)
            text, meta, emitted = _emit(deduped, emitted, reduce_meta, cfg, budget,
                                        common)

    if store and deduped:
        stored, store_notes, store_extra = store_mod.store(
            deduped, meta, cfg, db=db, source=source, raw_path=path, raw_bytes=len(raw))
        if not stored or store_notes or store_extra:
            common["stored"] = stored          # optimism was wrong; re-render
            common["notes"] = notes + store_notes
            common["extra"] = {**(extra or {}), **store_extra}
            shown_before = [r.get("shown") for r in deduped]
            text, meta, emitted = _emit(deduped, emitted, reduce_meta, cfg,
                                        budget, common)
            # Notes lengthen the footer, so this second pass can trim further
            # rows. The DB already has the first pass's flags, and a row marked
            # shown there but withheld here is reachable by neither the slice
            # nor `--hidden` — it exists and nothing admits it. store() is
            # delete-then-insert, so re-running it simply corrects them.
            if stored and [r.get("shown") for r in deduped] != shown_before:
                store_mod.store(deduped, meta, cfg, db=db, source=source,
                                raw_path=path, raw_bytes=len(raw))
    return text, meta


def _crash(payload, preset, exc, sink, ctx, max_bytes=None):
    try:
        return _crash_inner(payload, preset, exc, sink, ctx, max_bytes)
    except Exception as inner_exc:
        # The crash handler itself failed — e.g. isinstance(payload, bytes)
        # raised because payload.__class__ raises (a lazy proxy, a deferred ORM
        # attribute), or a dict-subclass preset's own .get() raised. This is
        # the last line of defence: "no code path may raise out of render()"
        # has no carve-out for the handler that exists to enforce it.
        meta = {"preset": "raw", "detected": False, "kind": "records",
                "render_id": ("crash" + f"{id(payload):x}"[-7:]),
                "raw_total": 0, "total": 0, "shown": 0, "withheld": 0,
                "by_tier": {}, "shown_by_tier": {}, "groups": 0, "groups_shown": 0,
                "alert_count": 0, "alert_shown": 0, "alert_recall": 1.0,
                "alert_truncated": False, "fields_cut": {},
                "bytes_in": 0, "bytes_out": 0, "reduction": 0.0,
                "loss_reason": "renderer_error", "error_type": _type_name(exc),
                "full_data_path": None, "persisted": False, "stored": False,
                "persist_error": "unrepresentable", "notes": []}
        text = (f"compress: renderer failed ({_type_name(exc)}); the payload "
               f"could not be represented at all ({_type_name(inner_exc)} "
               "in the crash handler)\n"
               + footer_mod.build({"tiers": []}, {"name": "raw"}, meta))
        meta["bytes_out"] = len(text)
        return text, meta


def _crash_inner(payload, preset, exc, sink, ctx, max_bytes=None):
    if isinstance(preset, str):
        name = preset
    elif isinstance(preset, dict):
        name = preset.get("name", "raw")
    else:
        name = "raw"
    raw = _raw_bytes(payload)
    path, error = ctx.get("persisted"), ctx.get("error")
    if path is None and error is None:
        path, error = persist(raw, name or "raw", sink)
    meta = {"preset": name or "raw", "detected": False, "kind": "records",
            "render_id": hashlib.sha256(raw).hexdigest()[:12],
            "raw_total": 0, "total": 0, "shown": 0, "withheld": 0,
            "by_tier": {}, "shown_by_tier": {}, "groups": 0, "groups_shown": 0,
            "alert_count": 0, "alert_shown": 0, "alert_recall": 1.0,
            "alert_truncated": False, "fields_cut": {},
            "bytes_in": len(raw), "bytes_out": 0, "reduction": 0.0,
            "loss_reason": "renderer_error", "error_type": _type_name(exc),
            "full_data_path": path, "persisted": path is not None, "stored": False,
            "persist_error": error, "notes": []}
    # Fail open returns the payload (2.8) — but a context-capping renderer
    # handing back 1.2 MB is the failure mode it exists to prevent. Cut it to
    # the budget and say so; the full copy is on disk and the footer names it.
    budget = (max_bytes if max_bytes is not None else PRESET_DEFAULTS["max_bytes"])
    meta["max_bytes"] = budget
    body = _as_text(payload)
    if not body.endswith("\n"):
        body += "\n"
    foot = footer_mod.build({"tiers": []}, {"name": meta["preset"]}, meta)
    room = budget - bytelen(foot)
    if bytelen(body) > room:
        meta["payload_truncated"] = True
        foot = footer_mod.build({"tiers": []}, {"name": meta["preset"]}, meta)
        body = truncate_bytes(body, budget - bytelen(foot))
        if body and not body.endswith("\n"):
            body += "\n"
            body = truncate_bytes(body, budget - bytelen(foot))
    text = body + foot
    meta["bytes_out"] = bytelen(text)
    return text, meta


def render_lines(text, preset="logs", max_bytes=None, **kw):
    from .adapters.lines import render_lines as _render_lines
    return _render_lines(text, preset=preset, max_bytes=max_bytes, **kw)


# Last, and eagerly: fetch.py imports names defined above, and loading it binds
# `compress.fetch` to the submodule — this rebinds the name back to the function,
# so `from .._compress import fetch` (6) keeps working however it is imported.
from .fetch import fetch  # noqa: E402
