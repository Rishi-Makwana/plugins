#!/usr/bin/env python3
"""
compress-check — should this plugin's output go through the ldm-compress renderer?

    python3 compress_check.py out.json
    snyk test --json | python3 compress_check.py
    python3 compress_check.py out.json --json          # machine-readable
    python3 compress_check.py out.json --max-bytes 12288

Exit: 0 USE · 1 SKIP · 2 BORDERLINE · 3 error
Stdlib only. Reads a file or stdin — never runs commands itself.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter

MAX_BYTES = 12288          # renderer's lossless threshold; below this nothing is cut
BYTES_PER_TOKEN = 4        # rough — always shown as ≈
PROJECT_KEEP = 0.5         # field projection keeps ~half of each record's bytes
CONTEXT_LINES = 3          # a log alert carries this many lines either side

# name, ordered vocabulary (index = rank), values treated as never-drop
LEXICONS = [
    ("severity", ["critical", "high", "medium", "moderate", "low", "negligible",
                  "info", "informational", "unknown"], {"critical", "high"}),
    ("sonar",    ["blocker", "critical", "major", "minor", "info"], {"blocker", "critical"}),
    ("sarif",    ["error", "warning", "note", "none"], {"error"}),
    ("log",      ["fatal", "panic", "error", "warn", "warning", "info", "debug", "trace"],
                 {"fatal", "panic", "error"}),
    ("test",     ["failed", "failure", "error", "passed", "pass", "skipped", "pending"],
                 {"failed", "failure", "error"}),
]

SIGNAL_RE = re.compile(r"(?i)\b(fatal|panic|error|err|fail|failed|failure|exception|traceback|critical|✗)\b")
WARN_RE = re.compile(r"(?i)\b(warn|warning|deprecated)\b")
CODE_RE = re.compile(r"^\s*(def |class |import |from \S+ import|const |let |var |function |return\b|#include|package |public |private |if \(|for \(|while \()|[;{}]\s*$")

# Structure, not vocabulary. Counting lines that merely CONTAIN "error" reads a
# document *about* error handling as a log: a spec scored 6.9% on that test with
# zero log lines in it. A real log instead carries a timestamp or a level token
# in a fixed position, and a document carries headings, bullets, tables, fences.
# Measured: log_struct 1.00 on a real GHA log vs 0.00 on two markdown specs;
# doc_struct 0.39-0.56 on those specs vs 0.00 on the log and 0.004 on source.
LOG_STRUCT_RE = re.compile(r"""^\s{0,3}(?:
      \[?\d{4}-\d{2}-\d{2}[T ]                        # ISO date
    | \[?\d{1,2}:\d{2}:\d{2}                          # clock
    | \#\#\[\w+\]                                     # GitHub Actions
    | \[?(?:FATAL|ERROR|WARN|WARNING|INFO|DEBUG|TRACE|NOTICE)\]?\s*[\s:\]\-]
    | [A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}   # syslog
)""", re.I | re.X)

DOC_STRUCT_RE = re.compile(r"""^\s{0,3}(?:
      \#{1,6}\s | [-*+]\s | \d+\.\s | >\s | \| | ```|~~~   # markdown
    | !?\[[^\]]+\]\(                                       # markdown link/image
    | [=~^"'*+#-]{3,}\s*$                                  # rst/setext underline
    | \.\.\s                                               # rst directive
    | :[A-Za-z][\w-]*:\s                                   # rst field list
)""", re.X)

USE, SKIP, BORDERLINE, ERROR = 0, 1, 2, 3
LABEL = {USE: "✔ USE", SKIP: "✘ SKIP", BORDERLINE: "~ BORDERLINE"}


# ── helpers ──────────────────────────────────────────────────────────────────

def kb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def tokens(n: int) -> str:
    return f"≈{round(n / BYTES_PER_TOKEN):,} tokens"


def pct(x: float) -> str:
    return f"{x:.1f}%" if 0 < x < 1 or 99 < x < 100 else f"{x:.0f}%"


def _get(r, path):
    for p in path.split("."):
        r = r.get(p) if isinstance(r, dict) else None
    return r


# ── JSON path ────────────────────────────────────────────────────────────────

def find_records(obj):
    """Largest array of ≥3 dicts anywhere in the tree → (path, records) or None."""
    found = []

    def walk(o, path, depth):
        if isinstance(o, list):
            if len(o) >= 3 and sum(isinstance(x, dict) for x in o) >= 0.8 * len(o):
                found.append((path or "$", depth, o))
            for i, x in enumerate(o[:40]):
                walk(x, f"{path}[{i}]", depth + 1)
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k, depth + 1)

    walk(obj, "", 0)
    if not found:
        return None
    path, _, recs = max(found, key=lambda t: (len(t[2]), -t[1]))
    return path, recs


def _str_fields(records):
    """Fields (one nesting level allowed) that are strings in ≥80% of records."""
    c = Counter()
    for r in records:
        for k, v in r.items():
            if isinstance(v, str):
                c[k] += 1
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, str):
                        c[f"{k}.{k2}"] += 1
    return [k for k, n in c.items() if n >= 0.8 * len(records)]


def infer_rank(records):
    """Field whose values come from a known severity/level vocabulary."""
    best = None
    for f in _str_fields(records):
        vals = Counter(str(_get(r, f) or "").lower() for r in records)
        distinct = set(vals) - {""}
        if not distinct or len(distinct) > 12:
            continue
        for name, lex, alerts in LEXICONS:
            hit = distinct & set(lex)
            if hit and len(hit) >= 0.6 * len(distinct):
                score = sum(vals[v] for v in hit)
                if best is None or score > best[0]:
                    best = (score, f, name, lex, alerts, vals)
    return best


def sniff_preset(data):
    d = data
    first = d[0] if isinstance(d, list) and d and isinstance(d[0], dict) else None
    if isinstance(d, dict):
        if "sarif" in str(d.get("$schema", "")).lower() or ("runs" in d and "version" in d):
            return "sarif"
        if "vulnerabilities" in d and "packageManager" in d:
            return "snyk"
        if "issues" in d and ("components" in d or "paging" in d):
            return "sonarqube"
        if "Results" in d and "ArtifactName" in d:
            return "trivy"
        if "items" in d and "apiVersion" in d:
            return "k8s"
        if "testsuites" in d or "testsuite" in d:
            return "junit"
    if first is not None:
        if "security_advisory" in first:
            return "dependabot"
        if {"number", "title"} <= first.keys():
            return "gh_api"
    return None


def analyze_json(data, raw_bytes, max_bytes):
    m = {"kind": "json", "shape": "json blob", "records": 0, "records_path": None,
         "rank_field": None, "tiers": {}, "alerts": 0, "alert_pct": 0.0,
         "preset": sniff_preset(data)}
    hit = find_records(data)
    if not hit:
        m["alert_bytes"] = 0
        return m
    path, recs = hit
    n = len(recs)
    m.update(shape="json records", records=n, records_path=path)
    avg_rec = raw_bytes / n
    rank = infer_rank(recs)
    if rank:
        _, field, _, lex, alert_vals, vals = rank
        tiers = {v: vals[v] for v in lex if vals.get(v)}
        other = sum(c for v, c in vals.items() if v and v not in lex)
        if other:
            tiers["other"] = other
        alerts = sum(vals.get(v, 0) for v in alert_vals)
        m.update(rank_field=field, tiers=tiers, alerts=alerts, alert_pct=100 * alerts / n)
        m["alert_bytes"] = alerts * avg_rec * PROJECT_KEEP
    else:
        # no rank field: renderer's autodetect would fall back to top-decile-by-score or nothing
        m["alert_bytes"] = 0
        m["shape"] = "json records (no rank field found)"
    return m


# ── text path ────────────────────────────────────────────────────────────────

def analyze_text(text, raw_bytes, max_bytes):
    lines = text.splitlines()
    ne = [l for l in lines if l.strip()]
    m = {"kind": "text", "lines": len(lines), "records": len(ne), "preset": None,
         "rank_field": None, "tiers": {}, "alerts": 0, "alert_pct": 0.0, "alert_bytes": 0}
    if not ne:
        m["shape"] = "empty"
        return m

    # NDJSON? treat as records
    parsed = []
    for l in ne[:200]:
        try:
            o = json.loads(l)
            if isinstance(o, dict):
                parsed.append(o)
        except Exception:
            break
    if len(parsed) >= 0.8 * min(len(ne), 200) and len(parsed) >= 3:
        allp = []
        for l in ne:
            try:
                allp.append(json.loads(l))
            except Exception:
                pass
        j = analyze_json(allp, raw_bytes, max_bytes)
        j["shape"] = "ndjson records"
        j["kind"] = "text"
        j["lines"] = len(lines)
        return j

    lens = [len(l) for l in ne]
    avg = statistics.fmean(lens)
    pk = lambda l: re.sub(r"\d", "#", l.strip()[:10])
    repeat = 1 - len({pk(l) for l in ne}) / len(ne)
    signal = sum(1 for l in ne if SIGNAL_RE.search(l))
    warn = sum(1 for l in ne if WARN_RE.search(l))
    sentence = sum(1 for l in ne if re.search(r"[a-z][.!?]['\")]?(\s|$)", l)) / len(ne)
    code = sum(1 for l in ne if CODE_RE.search(l)) / len(ne)
    log_struct = sum(1 for l in ne if LOG_STRUCT_RE.match(l)) / len(ne)
    doc_struct = sum(1 for l in ne if DOC_STRUCT_RE.match(l)) / len(ne)
    indent = sum(1 for l in ne if l[:1] in " \t") / len(ne)
    cols = Counter(len(re.split(r"\t|\||\s{2,}", l.strip())) for l in ne)
    top_cols, top_n = cols.most_common(1)[0]
    is_table = top_cols >= 3 and top_n >= 0.8 * len(ne)

    if is_table:
        shape = "table rows"
    elif code >= 0.3 and sentence < 0.3:
        shape = "source code"
    elif code >= 0.12 and indent >= 0.5 and sentence < 0.3 and log_struct < 0.1:
        # CODE_RE only matches keywords, so it scores real Python at 0.23 —
        # under the 0.3 bar — because docstrings, assignments and continuation
        # lines are most of a file. Deep indentation with almost no sentences
        # is the signal those lines share. A traceback stays "log lines": it is
        # indented but matches few code keywords, and it SHOULD be compressed.
        shape = "source code"
    elif doc_struct >= 0.15 and log_struct < 0.1:
        # Markdown/rst: headings, bullets, tables, fences, links. This has to be
        # tested BEFORE the log branch — a doc's `sentence` ratio is only ~0.15
        # (most lines are headings or bullets, not sentences ending in a period),
        # so the prose test below can never fire for one.
        shape = "prose"
    elif (sentence > 0.5 and repeat < 0.25) or avg > 220:
        shape = "prose"
    elif log_struct >= 0.1 or repeat >= 0.3 or (signal + warn) >= 0.01 * len(ne):
        shape = "log lines"
    else:
        shape = "unstructured text"

    tiers = {}
    if signal:
        tiers["error"] = signal
    if warn:
        tiers["warn"] = warn
    tiers["other"] = len(ne) - signal - warn
    kept = min(len(ne), signal * (1 + 2 * CONTEXT_LINES))
    m.update(shape=shape, tiers=tiers, alerts=signal,
             alert_pct=100 * signal / len(ne), alert_bytes=kept * avg,
             avg_line=avg, repeat=repeat, sentence=sentence)
    return m


# ── verdict ──────────────────────────────────────────────────────────────────

def decide(m, raw_bytes, max_bytes):
    shape = m["shape"]
    if shape == "empty":
        return SKIP, "Empty output. Nothing to do."
    if shape == "prose":
        return SKIP, "Reads as prose — no rows to rank. That's a search/chunking problem, not a reducing one."
    if shape == "source code":
        return SKIP, "Looks like source code. You need it verbatim — read it directly."
    if raw_bytes <= max_bytes:
        return SKIP, f"Only {kb(raw_bytes)} — under the {kb(max_bytes)} lossless threshold. Nothing would be cut."
    if shape == "json blob":
        return BORDERLINE, "Big, but no record array found. The renderer would only persist it to disk and show an outline."
    if m["alert_bytes"] > max_bytes:
        return BORDERLINE, (f"{pct(m['alert_pct'])} of it is high-priority. The renderer keeps all of that, "
                            f"so the budget overflows. Raise --max-bytes or narrow the alert rule.")
    reduction = 1 - max_bytes / raw_bytes
    if reduction < 0.4:
        return BORDERLINE, f"Only ~{reduction:.0%} smaller — it's barely over threshold. Marginal."
    if shape == "unstructured text":
        return BORDERLINE, "Big, but structure is unclear. Autodetect would run — check the footer says what you expect."
    if shape.startswith("json records (no rank"):
        return BORDERLINE, "Rows found but no severity/level field. It would reduce, but can't tell what matters — write a preset."
    low = 100 - m["alert_pct"]
    return USE, f"Large, {shape}, {pct(low)} low-priority. ~{reduction:.0%} smaller with every alert kept."


# ── output ───────────────────────────────────────────────────────────────────

def render(name, m, raw_bytes, max_bytes, code, reason):
    reduction = max(0.0, 1 - max_bytes / raw_bytes) if raw_bytes > max_bytes else 0.0
    rows = []
    rows.append(("size", f"{kb(raw_bytes)}  ·  {tokens(raw_bytes)}  ·  {m.get('lines', m.get('records', 0)):,} lines"
                 if m["kind"] == "text" else
                 f"{kb(raw_bytes)}  ·  {tokens(raw_bytes)}"))
    shape = m["shape"]
    if m.get("records") and m["kind"] == "json" or shape == "ndjson records":
        shape += f" — {m['records']:,} in `{m['records_path']}`"
    rows.append(("shape", shape))
    if m["tiers"] and set(m["tiers"]) != {"other"}:
        t = " · ".join(f"{k} {v:,}" for k, v in m["tiers"].items())
        rows.append(("rank", f"{m['rank_field']}: {t}" if m["rank_field"] else t))
    if m["alerts"]:
        unit = "lines" if m["kind"] == "text" and m["rank_field"] is None else "rows"
        rows.append(("alerts", f"{m['alerts']:,} {unit} kept no matter what  ({pct(m['alert_pct'])})"))
    if code != SKIP:
        rows.append(("projected", f"{kb(raw_bytes)} → ≤{kb(max_bytes)}  (~{reduction:.0%} smaller)"))
    if m.get("preset"):
        rows.append(("preset", m["preset"]))
    rows.append(("tax", f"{tokens(raw_bytes)} re-sent on every later turn"))

    w = max(len(k) for k, _ in rows)
    out = [f"compress-check · {name}", ""]
    out += [f"  {k.ljust(w)}   {v}" for k, v in rows]
    out += ["", f"  {LABEL[code]}", f"    {reason}", ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="captured output; omit to read stdin")
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES, help=f"renderer budget (default {MAX_BYTES})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    try:
        if a.file:
            raw = open(a.file, "rb").read()
            name = a.file
        else:
            raw = sys.stdin.buffer.read()
            name = "stdin"
    except OSError as e:
        print(f"compress-check: {e}", file=sys.stderr)
        return ERROR

    text = raw.decode("utf-8", errors="replace")
    raw_bytes = len(raw)

    data = None
    s = text.lstrip()
    if s[:1] in "{[":
        try:
            data = json.loads(text)
        except Exception:
            data = None

    try:
        m = analyze_json(data, raw_bytes, a.max_bytes) if data is not None else analyze_text(text, raw_bytes, a.max_bytes)
        code, reason = decide(m, raw_bytes, a.max_bytes)
    except Exception as e:  # never let the checker itself be the failure
        print(f"compress-check: analysis failed: {e}", file=sys.stderr)
        return ERROR

    if a.json:
        m = {k: v for k, v in m.items() if k not in ("avg_line", "repeat", "sentence")}
        m.update(file=name, bytes=raw_bytes, tokens_approx=round(raw_bytes / BYTES_PER_TOKEN),
                 max_bytes=a.max_bytes, verdict=LABEL[code].split()[1], reason=reason, exit=code)
        m["alert_bytes"] = round(m["alert_bytes"])
        print(json.dumps(m, indent=2))
    else:
        print(render(name, m, raw_bytes, a.max_bytes, code, reason))
    return code


if __name__ == "__main__":
    sys.exit(main())
