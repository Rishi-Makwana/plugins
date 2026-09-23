"""Log lines as records: tier by regex, alerts carry context, body is raw text."""
from __future__ import annotations

import hashlib
import re

from ..classify import is_alert, sort_key
from ..persist import persist
from ..reduce import reduce

_TS_RE = re.compile(r"^\S*\d{2}:\d{2}:\d{2}\S*\s*")
_GHA_RE = re.compile(r"^##\[\w+\]\s*")
_RX_CACHE: dict[str, object] = {}


def _rx(pattern):
    rx = _RX_CACHE.get(pattern)
    if rx is None:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = False
        _RX_CACHE[pattern] = rx
    return rx


def tier_of_line(text: str, rank_cfg: dict) -> tuple[int, str]:
    order = rank_cfg.get("regex_order") or []
    labels = rank_cfg.get("labels") or []
    for i, pattern in enumerate(order):
        rx = _rx(pattern)
        if rx and rx.search(text):
            return i, (labels[i] if i < len(labels) else f"tier{i}")
    default = rank_cfg.get("default", len(order))
    return default, (labels[default] if default < len(labels) else "other")


def group_of_line(text: str, group_by) -> str:
    stripped = _GHA_RE.sub("", _TS_RE.sub("", text))
    if not group_by:
        return ""
    pattern = group_by.get("regex") if isinstance(group_by, dict) else None
    if not pattern:
        return ""
    rx = _rx(pattern)
    m = rx.search(stripped) if rx else None
    return m.group(1) if (m and m.groups()) else "(none)"


def line_bytes(rec) -> str:
    """What one emitted line actually costs, header included.

    reduce budgets `bytelen(line_fn(r))`, and build_body emits a `# L<n>` run
    header before every non-contiguous row. Charging only the line understated
    the slice by ~35% — a log with a blank line between entries makes every row
    its own run (blanks are dropped as records but still advance the line
    number), so the worst case is one header each. Budget for the worst case:
    over-reserving shows fewer lines, under-reserving breaks the byte cap.
    """
    return f"# L{rec['_m'].get('line', 0)}\n{rec['line']}"


def build_body(emitted, records, reduce_meta):
    """Raw lines under `# L<n>` headers, one header per contiguous run."""
    rows = sorted(emitted, key=lambda r: r["_m"]["line"])
    out, prev = [], None
    for rec in rows:
        n = rec["_m"]["line"]
        if prev is None or n != prev + 1:
            out.append(f"# L{n}")
        out.append(rec["line"])
        prev = n
    return "\n".join(out) + ("\n" if out else "")


def render_lines(text, preset="logs", max_bytes=None, sink=None, store=True,
                 source=None, db=None, fell_back=False, raw=None, **_kw):
    from .. import PRESETS, _emit, project, with_defaults
    from .. import store as store_mod

    if isinstance(preset, dict):
        cfg = with_defaults(preset)
    else:
        cfg = with_defaults(PRESETS.get(preset if isinstance(preset, str) else "logs")
                            or PRESETS["logs"])
    if cfg.get("kind") != "lines":
        cfg = with_defaults(PRESETS["logs"])

    raw = raw if raw is not None else text.encode("utf-8", "replace")
    render_id = hashlib.sha256(raw).hexdigest()[:12]
    path, error = persist(raw, cfg["name"], sink)

    # Blank lines are not records; _m.line keeps the true 1-based file line number.
    rows = [{"line": ln, "_n_line": i + 1}
            for i, ln in enumerate(text.splitlines()) if ln.strip()]
    numbers = [r.pop("_n_line") for r in rows]
    projected, fields_cut = project(rows, cfg)

    rank = cfg.get("rank") or {}
    for i, rec in enumerate(projected):
        source_line = rows[i]["line"]
        tier, label = tier_of_line(source_line, rank)
        core = is_alert(rec, cfg.get("alert"), tier)
        rec["_m"].update(tier=tier, label=label, alert=core, alert_core=core,
                         index=i, line=numbers[i], score=0.0, ident=None,
                         key=sort_key(rec, tier, cfg.get("score"), i),
                         grp=group_of_line(source_line, cfg.get("group_by")))

    # Alert context: union the windows so no line is emitted twice.
    context = int(cfg.get("context_lines") or 0)
    if context:
        keep = set()
        for i, rec in enumerate(projected):
            if rec["_m"]["alert_core"]:
                keep.update(range(max(0, i - context), min(len(projected), i + context + 1)))
        for i in keep:
            projected[i]["_m"]["alert"] = True

    budget = (max_bytes if max_bytes is not None
              else cfg.get("max_bytes") or 12288)
    emitted, reduce_meta = reduce(projected, cfg, budget, line_fn=line_bytes)

    notes = []
    if fell_back:
        notes.append("could not parse as json/ndjson/junit; rendered as lines")
    if not projected:
        reduce_meta["loss_reason"] = "empty"
    elif fell_back and reduce_meta["loss_reason"] == "lossless":
        reduce_meta["loss_reason"] = "parse_fallback"

    common = dict(raw_total=len(projected), bytes_in=len(raw), fields_cut=fields_cut,
                  render_id=render_id, persisted_path=path, persist_error=error,
                  detected=False, rank_field="line", kind="lines", stored=False,
                  notes=notes, extra={"empty_path": None, "structure": None}
                  if not projected else None)

    # Store after the final trim, so the DB's `shown` flags match the footer.
    if store and projected:
        common["stored"] = True
    out, meta, emitted = _emit(projected, emitted, reduce_meta, cfg, budget, common,
                               body_fn=build_body)
    if store and projected:
        stored, store_notes, store_extra = store_mod.store(
            projected, meta, cfg, db=db, source=source, raw_path=path,
            raw_bytes=len(raw))
        if not stored or store_notes or store_extra:
            common["stored"] = stored
            common["notes"] = notes + store_notes
            common["extra"] = {**(common.get("extra") or {}), **store_extra}
            shown_before = [r.get("shown") for r in projected]
            out, meta, emitted = _emit(projected, emitted, reduce_meta, cfg, budget,
                                       common, body_fn=build_body)
            # See __init__._render: a longer footer can trim more rows after the
            # write, stranding rows that neither the slice nor --hidden returns.
            if stored and [r.get("shown") for r in projected] != shown_before:
                store_mod.store(projected, meta, cfg, db=db, source=source,
                                raw_path=path, raw_bytes=len(raw))
    return out, meta
