"""The footer. Compression may hide records; it may never hide their existence."""
from __future__ import annotations

RULE = "─" * 60


def kb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def outline(obj) -> str:
    if isinstance(obj, dict):
        return "{" + ", ".join(k + ("[]" if isinstance(v, list) else "") for k, v in obj.items()) + "}"
    if isinstance(obj, list):
        head = outline(obj[0]) if obj else "?"
        return f"[{len(obj)} × {head}]"
    return type(obj).__name__


def coverage_of(records, preset, reduce_meta) -> dict:
    """Ordered (label, shown, total) per tier — every tier in the input, shown or not."""
    seen: dict[str, list] = {}
    for r in records:
        m = r["_m"]
        row = seen.setdefault(m["label"], [m["tier"], 0, 0])
        row[2] += 1
        if r.get("shown"):
            row[1] += 1
    tiers = [(label, row[1], row[2]) for label, row in
             sorted(seen.items(), key=lambda kv: (kv[1][0], kv[0]))]
    groups = {r["_m"]["grp"] for r in records if not r["_m"]["alert"]}
    return {"tiers": tiers, "groups": len(groups),
            "groups_shown": reduce_meta.get("groups_shown", 0)}


def assemble_meta(preset, records, emitted, reduce_meta, *, raw_total, bytes_in,
                  bytes_out, fields_cut, render_id, persisted_path, persist_error,
                  detected=False, rank_field=None, kind="records", stored=False,
                  notes=None, extra=None) -> dict:
    total = len(records)
    shown = sum(1 for r in records if r.get("shown"))
    by_tier: dict[str, int] = {}
    shown_by_tier: dict[str, int] = {}
    order: dict[str, int] = {}
    alert_count = alert_shown = 0
    for r in records:
        m = r["_m"]
        label = m["label"]
        order.setdefault(label, m["tier"])
        by_tier[label] = by_tier.get(label, 0) + 1
        shown_by_tier.setdefault(label, 0)
        if r.get("shown"):
            shown_by_tier[label] += 1
        if m["alert"]:
            alert_count += 1
            if r.get("shown"):
                alert_shown += 1
    keys = sorted(by_tier, key=lambda k: (order[k], k))
    by_tier = {k: by_tier[k] for k in keys}
    shown_by_tier = {k: shown_by_tier[k] for k in keys}
    groups = {r["_m"]["grp"] for r in records if not r["_m"]["alert"]}
    meta = {
        "preset": preset.get("name", "?"), "detected": bool(detected), "kind": kind,
        "render_id": render_id,
        "raw_total": raw_total, "total": total, "shown": shown, "withheld": total - shown,
        "by_tier": by_tier, "shown_by_tier": shown_by_tier,
        "groups": len(groups), "groups_shown": reduce_meta.get("groups_shown", 0),
        "alert_count": alert_count, "alert_shown": alert_shown,
        "alert_recall": 1.0 if alert_count == 0 else alert_shown / alert_count,
        "alert_truncated": bool(reduce_meta.get("alert_truncated")),
        "fields_cut": dict(fields_cut or {}),
        "bytes_in": bytes_in, "bytes_out": bytes_out,
        "reduction": 0.0 if not bytes_in else max(0.0, 1 - bytes_out / bytes_in),
        "loss_reason": reduce_meta.get("loss_reason", "budget"),
        "full_data_path": persisted_path, "persisted": persisted_path is not None,
        "stored": bool(stored),
        "rank_field": rank_field, "persist_error": persist_error,
        "notes": list(notes or []),
    }
    if extra:
        meta.update(extra)
    return meta


def _headline(preset, meta) -> str:
    total, alerts = meta["total"], meta["alert_count"]
    if meta.get("detected"):
        return (f"auto-detected · rank field: {meta.get('rank_field') or 'none'}"
                f" · {total} records · {alerts} alerts")
    dup = ""
    if meta["raw_total"] != total:
        dup = f" ({meta['raw_total']} raw, {meta['raw_total'] - total} deduped)"
    return f"{meta['preset']} · {total} records{dup} · {alerts} alerts"


def _tier_rows(tiers, compact=False) -> list[str]:
    if not tiers:
        return []
    if compact:
        # One line per tier is unbounded — a preset may declare 100 tiers, and
        # FOOTER_RESERVE is fixed. Rule 3 requires every tier to APPEAR, not to
        # get its own line, so fold them onto wrapped lines instead of dropping.
        cells = [f"{label} {shown}/{total}" for label, shown, total in tiers]
        out, line = [], "  "
        for cell in cells:
            candidate = cell if line == "  " else f"{line} · {cell}"
            if len(candidate) > 76 and line != "  ":
                out.append(line)
                line = "  " + cell
            else:
                line = candidate if line != "  " else "  " + cell
        if line.strip():
            out.append(line)
        return out
    lw = max(len(t[0]) for t in tiers)
    tw = max(len(str(t[2])) for t in tiers)
    out = []
    for label, shown, total in tiers:
        tick = "  ✓" if shown == total else ""
        out.append(f"  {label.ljust(lw)}{str(shown).rjust(5)} / {str(total).rjust(tw)}{tick}")
    return out


def _group_label(preset) -> str:
    gb = preset.get("group_by")
    if isinstance(gb, str):
        return gb
    if isinstance(gb, dict):
        return gb.get("path") or "pattern"
    return ""


def fetch_hint(preset, meta) -> str | None:
    """The way back in — that render's own rank field and its top withheld tier."""
    if meta["withheld"] <= 0 or not meta.get("stored") or not meta.get("render_id"):
        return None
    rank_path = (preset.get("rank") or {}).get("path")
    short = meta["render_id"][:6]
    pick = None
    for label, total in meta["by_tier"].items():
        if total - meta["shown_by_tier"].get(label, 0) > 0:
            pick = label
            break
    if rank_path and pick and pick != "other":
        return (f"fetch: compress_fetch {short} --where {rank_path}={pick}"
                f"   ·   --hidden")
    return f"fetch: compress_fetch {short} --hidden"


def _data_line(meta) -> str:
    if meta.get("full_data_path"):
        return f"full data: {meta['full_data_path']}"
    reason = meta.get("persist_error") or "unavailable"
    return f"full data: not persisted ({reason})"


def build(coverage, preset, meta, compact=False) -> str:
    reason = meta.get("loss_reason")
    lines = [RULE]

    if reason == "renderer_error":
        lines.append(f"⚠ renderer failed ({meta.get('error_type', 'Exception')})"
                     " — original payload returned above")
        if meta.get("payload_truncated"):
            lines.append(f"⚠ payload cut to fit the {meta.get('max_bytes', 0)}"
                         " byte budget — the full copy is on disk")
        lines.append(_data_line(meta))
        lines.append(RULE)
        return "\n".join(lines) + "\n"

    if meta.get("mode") == "fetch":
        lines.append(f"{meta['preset']} · fetch {meta.get('render_id', '')[:6]}"
                     f" · {meta.get('query', 'summary')}")
        lines.append(f"matched {meta['total']} · shown {meta['shown']}"
                     f" · from {meta.get('source_total', meta['total'])} records"
                     f" ({meta.get('source_shown', 0)} originally shown)")
    else:
        lines.append(_headline(preset, meta))
        if reason == "empty":
            where = meta.get("empty_path")
            lines.append(f"parsed OK; `{where}` was empty" if where
                         else "parsed OK; no records found")
            if meta.get("structure"):
                lines.append(f"structure: {meta['structure']}")
            for note in meta.get("notes", []):
                lines.append(note)
            lines.append(_data_line(meta))
            lines.append(RULE)
            return "\n".join(lines) + "\n"
        if reason == "lossless":
            lines.append(f"shown {meta['shown']} · withheld 0 · lossless")
        else:
            lines.append(f"shown {meta['shown']} · withheld {meta['withheld']}"
                         f" · {kb(meta['bytes_in'])} → {kb(meta['bytes_out'])}"
                         f" ({round(100 * meta['reduction'])}% reduction)")

    if meta.get("alert_truncated"):
        missing = meta["alert_count"] - meta["alert_shown"]
        lines.append(f"⚠ {missing} alerts withheld — budget too small")

    lines += _tier_rows(coverage.get("tiers", []), compact=compact)

    glabel = _group_label(preset)
    if glabel:
        lines.append(f"groups {coverage.get('groups', 0)}"
                     f" · shown {coverage.get('groups_shown', 0)} · by {glabel}")
    if meta.get("fields_cut"):
        cut = " · ".join(f"{k} ×{v}" for k, v in meta["fields_cut"].items())
        lines.append(f"fields cut: {cut}")
    lines.append(f"alert recall {meta['alert_recall']:.2f} · counts exact")
    for note in meta.get("notes", []):
        lines.append(note)
    lines.append(_data_line(meta))
    hint = meta.get("fetch_line") or fetch_hint(preset, meta)
    if hint:
        lines.append(hint)
    lines.append(RULE)
    return "\n".join(lines) + "\n"
