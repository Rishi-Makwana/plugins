from compress.dedupe import dedupe


def rows(n):
    return [{"id": f"i{i}", "pkg": "p", "version": "1"} for i in range(n)]


def test_collapses_and_counts():
    base = rows(200)
    extra = [dict(base[i]) for i in range(14)]
    kept, raw_total = dedupe(base + extra, ["id", "pkg", "version"])
    assert raw_total == 214
    assert len(kept) == 200
    assert kept[0]["_n"] == 2


def test_no_identity_falls_back_to_content_hash():
    kept, raw_total = dedupe([{"a": 1}, {"a": 1}, {"a": 2}], None)
    assert (raw_total, len(kept)) == (3, 2)
    assert kept[0]["_n"] == 2


def test_n_absent_when_unique():
    kept, _ = dedupe(rows(3), ["id"])
    assert all("_n" not in r for r in kept)


def test_unhashable_identity_values_do_not_raise():
    kept, _ = dedupe([{"id": [1, 2]}, {"id": [1, 2]}, {"id": [3]}], ["id"])
    assert len(kept) == 2


# A more severe duplicate must never vanish. Dedupe runs after classify_all,
# so severity lives in `_m`.
def test_a_more_severe_duplicate_survives_within_one_alert_state():
    low = {"id": "SNYK-1", "pkg": "lodash", "_m": {"tier": 3, "alert": False}}
    high = {"id": "SNYK-1", "pkg": "lodash", "_m": {"tier": 1, "alert": False}}
    kept, raw_total = dedupe([low, high], ["id", "pkg"])
    assert raw_total == 2 and len(kept) == 1
    assert kept[0] is high and kept[0]["_n"] == 2


def test_records_disagreeing_on_alert_worthiness_are_not_collapsed():
    """Collapsing them would either drop the alerting content or flag a
    survivor whose own data cannot justify the flag. Keep both instead."""
    quiet = {"id": "X", "_m": {"tier": 0, "alert": False}}
    alerting = {"id": "X", "_m": {"tier": 3, "alert": True}}
    kept, raw_total = dedupe([quiet, alerting], ["id"])
    assert raw_total == 2 and len(kept) == 2
    assert quiet in kept and alerting in kept
    assert alerting["_m"]["alert"] is True          # kept its own, unforced


def test_equal_severity_ties_keep_first_occurrence():
    first = {"id": "X", "_m": {"tier": 1, "alert": False}}
    second = {"id": "X", "_m": {"tier": 1, "alert": False}}
    kept, _ = dedupe([first, second], ["id"])
    assert kept[0] is first


def test_render_keeps_the_critical_when_a_duplicate_identity_disagrees_on_severity():
    import compress
    payload = {"packageManager": "npm", "vulnerabilities": [
        {"id": "SNYK-1", "packageName": "lodash", "version": "4.17.15",
         "severity": "low", "cvssScore": 2.0, "title": "minor"},
        {"id": "SNYK-1", "packageName": "lodash", "version": "4.17.15",
         "severity": "critical", "cvssScore": 9.9, "exploitMaturity": "mature",
         "title": "REMOTE CODE EXECUTION"},
        {"id": "SNYK-2", "packageName": "axios", "version": "1.0.0",
         "severity": "medium", "cvssScore": 5.0}]}
    text, meta = compress.render(payload, preset="snyk", store=False)
    assert meta["by_tier"].get("critical") == 1
    assert meta["alert_count"] == 1
    assert "REMOTE CODE EXECUTION" in text and "critical" in text


# D1/D12: a same-tier collision where only ONE duplicate is alert-worthy via a
# field predicate must keep THAT record's content — earlier attempts kept the
# harmless twin and OR-ed a flag onto it, producing a row filed under "alerts"
# whose own data explained nothing.
def test_same_tier_collision_keeps_the_alert_worthy_members_content():
    harmless = {"id": "X", "_m": {"tier": 1, "alert": False}}
    dangerous = {"id": "X", "_m": {"tier": 1, "alert": True}}
    kept, _ = dedupe([harmless, dangerous], ["id"])
    assert dangerous in kept


def test_no_survivor_is_ever_flagged_beyond_what_its_own_data_says():
    """Every member of a group shares one alert state, so nothing is forced."""
    harmless = {"id": "X", "_m": {"tier": 1, "alert": False, "alert_core": False}}
    dangerous = {"id": "X", "_m": {"tier": 3, "alert": True, "alert_core": True}}
    kept, _ = dedupe([harmless, dangerous], ["id"])
    assert len(kept) == 2
    for rec in kept:
        assert rec["_m"]["alert"] == rec["_m"]["alert_core"]


def test_render_same_tier_sarif_collision_is_fetchable_as_an_alert(tmp_path):
    import compress
    from compress.fetch import fetch
    db = str(tmp_path / "c.db")
    payload = {"$schema": "sarif-2.1.0", "runs": [{"tool": {"driver": {"name": "x"}},
              "results": [
        {"ruleId": "py/sql-injection", "level": "warning",
         "message": {"text": "harmless twin"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.py"},
                                              "region": {"startLine": 10}}}]},
        {"ruleId": "py/sql-injection", "level": "warning",
         "message": {"text": "the dangerous one"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.py"},
                                              "region": {"startLine": 10}}}],
         "properties": {"security-severity": "9.5"}},
    ]}]}
    text, meta = compress.render(payload, preset="sarif", db=db)
    assert meta["alert_count"] == 1
    assert "the dangerous one" in text
    _, fm = fetch(meta["render_id"], alerts=True, db=db)
    assert fm["matched"] == 1


# Regression: the O(k) rewrite must not have reintroduced O(k^2). A generous
# but bounded ceiling — this was ~35s under the old min()+.index() pairing.
def test_dedupe_stays_linear_on_one_giant_group():
    import time
    recs = [{"id": "r", "_m": {"tier": i % 4, "alert": False}} for i in range(50000)]
    t0 = time.perf_counter()
    kept, raw_total = dedupe(recs, ["id"])
    assert time.perf_counter() - t0 < 2.0
    assert raw_total == 50000 and len(kept) == 1


# --- identity canonicalisation: the dedupe key and stored `ident` must agree --

def test_ident_part_folds_number_types_but_not_booleans():
    from compress.classify import ident_part
    assert ident_part(9) == ident_part(9.0) == "9"
    assert ident_part("9.0") == "9.0"      # a string id is not the number 9
    assert ident_part(True) == "true" and ident_part(1) == "1"
    assert ident_part(None) == ""


def test_int_and_float_ids_are_one_identity():
    import compress
    # same alert state on both, so identity canonicalisation is what is measured
    _, meta = compress.render({"packageManager": "npm", "vulnerabilities": [
        {"id": 9, "packageName": "p", "version": "1", "severity": "low"},
        {"id": 9.0, "packageName": "p", "version": "1", "severity": "medium"}]},
        preset="snyk", store=False)
    assert meta["total"] == 1 and "medium" in meta["by_tier"]   # severer wins


def test_true_does_not_collapse_into_the_integer_one():
    import compress
    _, meta = compress.render({"packageManager": "npm", "vulnerabilities": [
        {"id": 1, "packageName": "p", "version": "1", "severity": "low"},
        {"id": True, "packageName": "p", "version": "1", "severity": "critical"}]},
        preset="snyk", store=False)
    assert meta["total"] == 2


def test_the_multi_project_case_actually_collapses():
    """snyk --all-projects reports one vuln once per project. The fixture used
    to contain zero identity collisions, so this case was never exercised."""
    import compress
    from conftest import expect, load
    exp = expect("snyk-multi.json")
    text, meta = compress.render(load("snyk-multi.json"), preset="snyk",
                                 max_bytes=12288, store=False)
    assert exp["raw_total"] == 36 and exp["total"] == 34
    assert meta["raw_total"] == 36 and meta["total"] == 34
    assert "36 records (36 raw" not in text
    assert '"_n":3' in text                      # the shared vuln, collapsed
    ident = exp["shared_vuln"]
    assert text.count(f'"{ident[0]}"') == 1      # emitted once, not three times
