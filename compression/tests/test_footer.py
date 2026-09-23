"""Every footer variant in 5.2 renders, and the trim loop holds the hard cap."""
import pytest

import compress
from compress.reduce import bytelen
from compress import footer as footer_mod
from conftest import expect, footer_of, load

RULE = "─" * 60


def snyk_payload(n, severity="low", groups=1, title=""):
    return {"ok": False, "packageManager": "npm", "vulnerabilities": [
        {"id": f"V-{i:04d}", "packageName": f"pkg-{i % groups}", "version": "1.0.0",
         "severity": severity, "cvssScore": 4.0, "title": title or f"issue {i}"}
        for i in range(n)]}


def test_rule_is_sixty_box_characters():
    assert footer_mod.RULE == RULE and len(footer_mod.RULE) == 60


def test_tier_rows_match_the_spec_layout():
    rows = footer_mod._tier_rows([("critical", 2, 2), ("high", 9, 9),
                                  ("medium", 6, 60), ("low", 5, 129)])
    assert rows == ["  critical    2 /   2  ✓",
                    "  high        9 /   9  ✓",
                    "  medium      6 /  60",
                    "  low         5 / 129"]


def test_lossless_variant():
    text, meta = compress.render(snyk_payload(3), preset="snyk", store=False)
    foot = footer_of(text)
    assert "shown 3 · withheld 0 · lossless" in foot
    assert "fetch: compress_fetch" not in foot        # nothing was withheld


def test_budget_variant_reports_bytes_and_reduction():
    text, _ = compress.render(load("snyk-200.json"), preset="snyk",
                              max_bytes=12288, store=False)
    foot = footer_of(text)
    assert "reduction)" in foot and " KB → " in foot
    assert "alert recall 1.00 · counts exact" in foot


def test_alert_overflow_variant():
    text, meta = compress.render(load("alert-overflow.json"), preset="snyk",
                                 max_bytes=4096, store=False)
    missing = meta["alert_count"] - meta["alert_shown"]
    assert f"⚠ {missing} alerts withheld — budget too small" in text


def test_autodetected_variant_names_the_rank_field():
    text, meta = compress.render(load("unknown-json.json"), max_bytes=12288, store=False)
    assert meta["detected"] is True
    assert text.splitlines()[text.splitlines().index(RULE) + 1].startswith(
        "auto-detected · rank field: level")


def test_empty_variant():
    text, _ = compress.render(load("empty.json"), preset="snyk", store=False)
    lines = footer_of(text).splitlines()
    assert lines[1] == "snyk · 0 records · 0 alerts"
    assert lines[2] == "parsed OK; `vulnerabilities` was empty"
    assert lines[3].startswith("structure: {ok, packageManager, vulnerabilities[]")


def test_parse_fallback_variant():
    text, _ = compress.render("{not json at all\nsecond line\n", store=False)
    assert "could not parse as json/ndjson/junit; rendered as lines" in text


def test_crash_variant(monkeypatch):
    monkeypatch.setattr(compress, "reduce", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    text, meta = compress.render(snyk_payload(2), preset="snyk", store=False)
    assert "⚠ renderer failed (ValueError) — original payload returned above" in text


def test_groups_line_only_when_grouping():
    text, _ = compress.render(snyk_payload(300, groups=300), preset="snyk",
                              max_bytes=12288, store=False)
    assert "· by packageName" in footer_of(text)


def test_fields_cut_is_reported():
    text, meta = compress.render(load("deep-trunc.json"), preset="snyk",
                                 max_bytes=12288, store=False)
    assert meta["fields_cut"].get("title")
    assert f"fields cut: title ×{meta['fields_cut']['title']}" in footer_of(text)


# 10 -- 300 groups: output still within budget, every tier still named
@pytest.mark.parametrize("budget", (2048, 4096, 12288))
def test_three_hundred_groups_stay_within_budget(budget):
    text, meta = compress.render(snyk_payload(300, groups=300), preset="snyk",
                                 max_bytes=budget, store=False)
    assert bytelen(text) <= budget
    assert meta["groups"] == 300
    for label, count in meta["by_tier"].items():
        if count:
            assert label in footer_of(text)


def test_trim_loop_drops_non_alerts_until_it_fits(monkeypatch):
    """Force an oversized footer; the loop must converge without dropping alerts."""
    real_build = footer_mod.build
    monkeypatch.setattr(footer_mod, "build",
                        lambda c, p, m, compact=False:
                        real_build(c, p, m, compact) + "─" * 3000 + "\n")
    payload = snyk_payload(200, groups=20)
    payload["vulnerabilities"][0]["severity"] = "critical"
    text, meta = compress.render(payload, preset="snyk", max_bytes=12288, store=False)
    assert bytelen(text) <= 12288
    assert meta["alert_shown"] == meta["alert_count"] == 1
    assert "V-0000" in text


def test_a_preset_with_many_tiers_still_fits_and_names_every_tier():
    """One footer line per tier is unbounded; rule 3 needs every tier NAMED."""
    from compress.reduce import bytelen
    order = [f"t{i:03d}" for i in range(120)]
    preset = {"name": "many", "fields": ["id", "sev"], "rows_path": ["rows"],
              "rank": {"path": "sev", "order": order, "default": 119},
              "alert": [{"rank_tier": 0}], "group_by": None}
    rows = [{"id": f"r{i}", "sev": order[i % 120]} for i in range(240)]
    text, meta = compress.render({"rows": rows}, preset=preset, max_bytes=4096,
                                 store=False)
    assert bytelen(text) <= 4096
    for label, n in meta["by_tier"].items():
        if n:
            assert label in text
