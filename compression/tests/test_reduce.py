"""Reduction: lossless under budget, alerts first, round robin across groups."""
from collections import Counter

from compress.reduce import FOOTER_RESERVE, reduce


def rows(n, group_of, alert_of, pad=40):
    out = []
    for i in range(n):
        rec = {"id": f"ID-{i:04d}", "g": group_of(i), "pad": "x" * pad}
        rec["_m"] = {"alert": alert_of(i), "grp": group_of(i),
                     "tier": 0 if alert_of(i) else 1, "label": "t",
                     "key": (0 if alert_of(i) else 1, 0.0, i), "index": i}
        out.append(rec)
    return out


def test_lossless_under_budget():
    recs = rows(10, lambda i: f"g{i % 3}", lambda i: False)
    emitted, meta = reduce(recs, {"group_by": "g"}, 64 * 1024)
    assert meta["loss_reason"] == "lossless"
    assert len(emitted) == len(recs)
    assert all(r["shown"] for r in recs)


def test_alerts_are_emitted_before_anything_else():
    recs = rows(40, lambda i: f"g{i % 4}", lambda i: i >= 36)
    emitted, _ = reduce(recs, {"group_by": "g"}, FOOTER_RESERVE + 600)
    first_non_alert = next(i for i, r in enumerate(emitted) if not r["_m"]["alert"])
    assert all(emitted[i]["_m"]["alert"] for i in range(first_non_alert))
    assert sum(1 for r in emitted if r["_m"]["alert"]) == 4


def test_alert_overflow_sets_the_flag():
    recs = rows(30, lambda i: "g", lambda i: True, pad=120)
    emitted, meta = reduce(recs, {"group_by": "g"}, FOOTER_RESERVE + 300)
    assert meta["alert_truncated"] is True
    assert meta["loss_reason"] == "alert_overflow"
    assert len(emitted) < 30


def test_alert_overflow_shows_zero_non_alerts():
    # Rule 1: "if alerts alone exceed budget, non-alerts go to zero first."
    # A record's own alert-worthiness must not make room for a low-priority one.
    alerts = rows(20, lambda i: "g", lambda i: True, pad=100)
    non_alerts = rows(10, lambda i: f"other{i}", lambda i: False, pad=10)
    emitted, meta = reduce(alerts + non_alerts, {"group_by": "g"}, FOOTER_RESERVE + 400)
    assert meta["alert_truncated"] is True
    assert all(r["_m"]["alert"] for r in emitted)
    assert meta["groups_shown"] == 0
    assert not any(r.get("shown") for r in non_alerts)


def test_noisy_group_takes_at_most_half_the_non_alert_slots():
    # 80% of records live in one group
    recs = rows(50, lambda i: "big" if i < 40 else f"g{i}", lambda i: False)
    emitted, _ = reduce(recs, {"group_by": "g"}, FOOTER_RESERVE + 900)
    counts = Counter(r["_m"]["grp"] for r in emitted if not r["_m"]["alert"])
    non_alert = sum(counts.values())
    assert non_alert > 1
    assert counts["big"] <= 0.5 * non_alert


def test_shown_flags_sum_to_the_emitted_count():
    recs = rows(60, lambda i: f"g{i % 6}", lambda i: i < 3)
    emitted, _ = reduce(recs, {"group_by": "g"}, FOOTER_RESERVE + 700)
    assert sum(1 for r in recs if r["shown"]) == len(emitted)
    assert all(r["shown"] is False for r in recs if r not in emitted)


def test_empty_input_reports_empty():
    emitted, meta = reduce([], {"group_by": "g"}, 12288)
    assert emitted == [] and meta["loss_reason"] == "empty"


# --- byte-accurate budgeting (max_bytes is BYTES, not characters) ------------

def test_bytelen_counts_utf8_bytes_not_characters():
    from compress.reduce import bytelen
    assert bytelen("abc") == 3
    assert bytelen("漏洞") == 6            # 3 bytes each
    assert bytelen("é") == 2


def test_truncate_bytes_never_splits_a_character():
    from compress.reduce import bytelen, truncate_bytes
    text = "漏洞漏洞"
    for limit in range(0, bytelen(text) + 2):
        cut = truncate_bytes(text, limit)
        assert bytelen(cut) <= limit
        assert cut == text[:len(cut)]      # a clean prefix, never mojibake


def test_a_non_ascii_payload_respects_the_byte_budget():
    import compress
    from compress.reduce import bytelen
    vulns = [{"id": f"V-{i}", "packageName": "包", "version": "1",
              "severity": "medium", "title": "漏洞" * 30} for i in range(400)]
    text, _ = compress.render({"packageManager": "npm", "vulnerabilities": vulns},
                              preset="snyk", max_bytes=12288, store=False)
    assert bytelen(text) <= 12288          # was ~19.5k bytes when measured in chars


def test_the_footer_reserve_never_swallows_the_whole_budget():
    import compress
    text, meta = compress.render(
        {"packageManager": "npm", "vulnerabilities": [
            {"id": f"V-{i}", "packageName": "p", "version": "1", "severity": "critical"}
            for i in range(10)]},
        preset="snyk", max_bytes=1024, store=False)
    assert meta["shown"] > 0               # a fixed 1024 reserve left budget 0
