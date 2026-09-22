"""The render pipeline end to end: the invariant, the fallbacks, the fail-open."""
import json

import pytest

import compress
from compress.reduce import bytelen
from conftest import all_fixtures, expect, fixture_names, footer_of, load

BUDGETS = (1024, 4096, 12288)


def render_fixture(name, budget=12288):
    exp = expect(name)
    return compress.render(load(name), preset=exp["preset"], max_bytes=budget,
                           store=False)


# 3 -- lossless
def test_lossless_keeps_every_record():
    payload = {"ok": False, "packageManager": "npm", "vulnerabilities": [
        {"id": f"V-{i}", "packageName": "p", "version": "1", "severity": "low"}
        for i in range(5)]}
    text, meta = compress.render(payload, preset="snyk", store=False)
    assert meta["loss_reason"] == "lossless"
    assert meta["withheld"] == 0 and meta["shown"] == meta["total"] == 5
    assert "withheld 0" in text and "lossless" in text
    for i in range(5):
        assert f"V-{i}" in text


# 4 -- tail-planted alerts survive aggressive compression
@pytest.mark.parametrize("name,exp", all_fixtures())
def test_planted_alerts_survive_at_2048(name, exp):
    text, _ = render_fixture(name, 2048)
    for planted in exp.get("planted", []):
        assert str(planted) in text, f"{name}: lost planted {planted}"


# 5 -- alert overflow states the exact withheld count and never raises
def test_alert_overflow_is_stated_not_hidden():
    text, meta = render_fixture("alert-overflow.json", 1024)
    assert meta["alert_truncated"] is True
    missing = meta["alert_count"] - meta["alert_shown"]
    assert f"alerts withheld" in text and str(missing) in text
    assert meta["alert_recall"] < 1.0          # the raw gate fails, by design
    assert expect("alert-overflow.json")["expect_alert_truncated"] is True


# 6 -- counts are exact at every budget
@pytest.mark.parametrize("name,exp", all_fixtures())
@pytest.mark.parametrize("budget", BUDGETS)
def test_counts_are_exact(name, exp, budget):
    _, meta = render_fixture(name, budget)
    assert meta["shown"] + meta["withheld"] == meta["total"]
    assert sum(meta["by_tier"].values()) == meta["total"]
    assert sum(meta["shown_by_tier"].values()) == meta["shown"]
    if exp["by_tier"] is not None:
        assert meta["by_tier"] == exp["by_tier"]
    assert meta["raw_total"] == exp["raw_total"] and meta["total"] == exp["total"]


# 7 -- every non-empty tier is named in the footer, at every budget
@pytest.mark.parametrize("name,exp", all_fixtures())
@pytest.mark.parametrize("budget", BUDGETS)
def test_no_non_empty_tier_is_missing(name, exp, budget):
    text, meta = render_fixture(name, budget)
    foot = footer_of(text)
    for label, count in meta["by_tier"].items():
        if count:
            assert label in foot, f"{name}@{budget}: tier {label} missing from footer"


# 8 -- dedupe totals both appear
def test_dedupe_totals_are_both_in_the_footer():
    text, meta = render_fixture("snyk-200.json")
    assert (meta["raw_total"], meta["total"]) == (214, 200)
    assert "200 records (214 raw, 14 deduped)" in text
    assert '"_n":' in text or any("_n" in line for line in text.splitlines())


# 11 -- empty and bad rows_path both explain themselves
def test_empty_names_the_path_and_outlines_the_structure():
    text, meta = render_fixture("empty.json")
    assert meta["loss_reason"] == "empty"
    assert "parsed OK; `vulnerabilities` was empty" in text
    assert "structure: {ok, packageManager, vulnerabilities[], summary}" in text


def test_bad_rows_path_outlines_the_keys_actually_present():
    text, meta = compress.render({"findings": [{"id": 1}], "meta": {"n": 1}},
                                 preset="snyk", store=False)
    assert meta["total"] == 0
    assert "findings[]" in text and "meta" in text


# 12 -- fallback chain
def test_parse_chain_json_ndjson_junit_lines():
    data, kind, fell = compress.parse('{"a": [1]}')
    assert (data, kind, fell) == ({"a": [1]}, "records", False)

    nd = "\n".join(json.dumps({"i": i}) for i in range(3))
    data, kind, fell = compress.parse(nd)
    assert kind == "records" and data == [{"i": 0}, {"i": 1}, {"i": 2}]

    data, kind, fell = compress.parse(load("junit-120.xml").decode())
    assert kind == "junit" and data[0]["suite"] == "suite-0"

    data, kind, fell = compress.parse("plain log line\nanother line")
    assert kind == "lines" and fell is False

    data, kind, fell = compress.parse('{"broken": ')
    assert kind == "lines" and fell is True


def test_junit_xml_renders_through_the_junit_preset():
    text, meta = compress.render(load("junit-120.xml"), store=False)
    assert meta["preset"] == "junit" and meta["total"] == 120


def test_unparseable_json_says_so_in_the_footer():
    text, meta = compress.render('{"broken": [1, 2', store=False)
    assert "could not parse as json/ndjson/junit; rendered as lines" in text


# 14 -- fail open
def test_renderer_crash_returns_the_original_payload(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(compress, "reduce", boom)
    payload = '{"vulnerabilities": [{"id": "A", "severity": "critical"}]}'
    text, meta = compress.render(payload, preset="snyk", store=False)
    assert meta["loss_reason"] == "renderer_error"
    assert meta["error_type"] == "RuntimeError"
    assert payload in text
    assert "renderer failed (RuntimeError)" in text


def test_no_code_path_raises_out_of_render():
    for payload in (None, "", b"", [], {}, 17, {"a": object()}, "\x00\xff"):
        text, meta = compress.render(payload, store=False)
        assert isinstance(text, str) and isinstance(meta, dict)
        assert "loss_reason" in meta


def test_render_survives_an_unpaired_surrogate_in_a_parsed_payload():
    # json.loads accepts a lone UTF-16 surrogate; UTF-8 cannot encode it. Both
    # the normal path (json.dumps→encode) and the crash path hit this.
    parsed = json.loads(
        r'{"packageManager":"npm","vulnerabilities":'
        r'[{"id":"\ud800x","packageName":"p","version":"1","severity":"critical"}]}')
    text, meta = compress.render(parsed, preset="snyk", store=False)
    assert isinstance(text, str) and meta["loss_reason"] == "lossless"


def test_render_survives_a_circular_reference():
    payload = {"vulnerabilities": [{"id": "A", "severity": "critical"}]}
    payload["self"] = payload
    text, meta = compress.render(payload, preset="snyk", store=False)
    assert isinstance(text, str) and isinstance(meta, dict)


def test_render_survives_a_value_whose_str_and_repr_both_raise():
    class Angry:
        def __str__(self):
            raise RuntimeError("nope")

        def __repr__(self):
            raise RuntimeError("nope repr too")

    payload = {"vulnerabilities": [{"id": "A", "severity": "critical", "weird": Angry()}]}
    text, meta = compress.render(payload, preset="snyk", store=False)
    assert isinstance(text, str) and isinstance(meta, dict)


# 15 -- determinism
@pytest.mark.parametrize("name", fixture_names())
def test_same_input_twice_gives_identical_bytes(name):
    a, ma = render_fixture(name, 4096)
    b, mb = render_fixture(name, 4096)
    assert a == b and ma == mb


# footer always emitted (rule 4)
@pytest.mark.parametrize("name,exp", all_fixtures())
@pytest.mark.parametrize("budget", BUDGETS)
def test_a_footer_is_always_emitted(name, exp, budget):
    text, _ = render_fixture(name, budget)
    assert footer_of(text).count("─" * 60) == 2


@pytest.mark.parametrize("name,exp", all_fixtures())
@pytest.mark.parametrize("budget", BUDGETS)
def test_output_never_exceeds_the_budget(name, exp, budget):
    # BYTES, not characters. "─" is 3 bytes, so the 60-char rule alone is 180
    # and len() undercounts every footer by 240 — this assertion passed on 8
    # fixtures that were overrunning their budget by up to 17%.
    text, _ = render_fixture(name, budget)
    assert bytelen(text) <= budget


# Regression (D2, second audit): the crash path's "unrepresentable" fallback
# must not be a constant string — two DIFFERENT such payloads hashed to the
# same render_id, and storing the second silently deleted the first's rows.
def test_two_unrepresentable_payloads_get_different_render_ids():
    class Angry:
        def __str__(self):
            raise RuntimeError("nope")

        def __repr__(self):
            raise RuntimeError("nope repr too")

    p1 = {"vulnerabilities": [{"id": "AAA-1", "severity": "critical", "weird": Angry()}]}
    p2 = {"vulnerabilities": [{"id": "BBB-2", "severity": "critical", "weird": Angry()}]}
    _, m1 = compress.render(p1, preset="snyk", store=False)
    _, m2 = compress.render(p2, preset="snyk", store=False)
    assert m1["render_id"] != m2["render_id"]


def test_a_second_unrepresentable_render_does_not_delete_the_firsts_rows(tmp_path):
    class Angry:
        def __str__(self):
            raise RuntimeError("nope")

        def __repr__(self):
            raise RuntimeError("nope repr too")

    db = str(tmp_path / "c.db")
    good = {"vulnerabilities": [{"id": "GOOD-1", "packageName": "p", "version": "1",
                                "severity": "critical"}]}
    poison = {"vulnerabilities": [{"id": "BAD-1", "severity": "critical", "weird": Angry()}]}
    _, m1 = compress.render(good, preset="snyk", db=db)
    assert m1["stored"] is True
    compress.render(poison, preset="snyk", db=db)          # must not touch m1's rows
    from compress.fetch import fetch
    _, fm = fetch(m1["render_id"], db=db)
    assert fm["exit"] == 0 and fm["matched"] == 1


# Regression (D3, second audit): the crash handler itself must not be able to
# raise. isinstance(payload, bytes) falls back to consulting payload.__class__
# when it differs from type(payload) — a class whose __class__ is a raising
# property (a lazy-proxy pattern: Django's SimpleLazyObject, werkzeug's
# LocalProxy) poisons isinstance() against ANY type, not just its own.
def test_render_survives_a_payload_whose_class_check_raises():
    class Poisoned:
        @property
        def __class__(self):
            raise RuntimeError("class boom")

    text, meta = compress.render(Poisoned(), preset="snyk", store=False)
    assert isinstance(text, str) and isinstance(meta, dict)
    assert meta["loss_reason"] == "renderer_error"


def test_the_crash_path_respects_max_bytes():
    """Fail-open returns the payload — but capped, with the cut declared."""
    from compress.reduce import bytelen
    payload = {"vulnerabilities": [{"id": f"V-{i}", "severity": "critical",
                                    "blob": "y" * 400} for i in range(3000)]}
    payload["poison"] = {1, 2}          # a set: reduce's json.dumps has no default=
    text, meta = compress.render(payload, preset="snyk", max_bytes=12288, store=False)
    assert bytelen(text) <= 12288       # was ~1.2 MB, 104x over


def test_the_fallback_honours_a_values_own_redaction():
    """json.dumps(default=str) would call __str__; the fallback must not use repr."""
    class Secret:
        def __init__(self, tok):
            self.tok = tok

        def __str__(self):
            return "***REDACTED***"

        def __repr__(self):
            return f"Secret(token={self.tok!r})"

    payload = {"vulnerabilities": [{"id": "V", "severity": "critical",
                                    "cfg": Secret("sk-live-DEADBEEF")}]}
    payload["self"] = payload           # a cycle forces the fallback
    text = compress._safe_text(payload)
    assert "sk-live-DEADBEEF" not in text
    assert "***REDACTED***" in text and "<cycle>" in text


def test_non_record_elements_are_counted_not_silently_dropped():
    text, meta = compress.render({"packageManager": "npm", "vulnerabilities": [
        {"id": "A", "packageName": "p", "version": "1", "severity": "critical"},
        "a bare string", 42, None, ["nested"],
        {"id": "B", "packageName": "q", "version": "1", "severity": "low"}]},
        preset="snyk", store=False)
    assert meta["total"] == 2
    assert "skipped 4 non-record element(s)" in text


class _Poisoned:
    """isinstance() consults __class__, so this reaches the last-resort fallback."""

    @property
    def __class__(self):
        raise RuntimeError("class boom")


# D9: id(payload) was reused the moment an object was freed, so sequential
# renders collided on render_id and store()'s delete-then-insert wiped an
# unrelated render. A non-content id must not key anything at all.
def test_an_unrepresentable_payload_is_never_persisted_or_stored(tmp_path):
    db = str(tmp_path / "c.db")
    good = {"packageManager": "npm", "vulnerabilities": [
        {"id": "KEEPME", "packageName": "p", "version": "1", "severity": "critical"}]}
    _, gm = compress.render(good, preset="snyk", db=db)
    assert gm["stored"] is True
    for _ in range(40):
        _, pm = compress.render(_Poisoned(), preset="snyk", db=db)
        assert pm["stored"] is False and pm["persisted"] is False
    from compress import store as store_mod
    conn = store_mod.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM renders WHERE render_id=?",
                            (gm["render_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM renders").fetchone()[0] == 1
    finally:
        conn.close()


# D10: the opposite failure — a per-call id broke re-render idempotence.
def test_unrepresentable_payloads_still_hash_consistently():
    ids = {compress.render(_Poisoned(), preset="snyk", store=False)[1]["render_id"]
           for _ in range(5)}
    assert len(ids) == 1


# D13: type(exc).__name__ can itself raise, inside the handler meant to catch.
def test_an_exception_whose_name_raises_does_not_escape():
    class BoomMeta(type):
        @property
        def __name__(cls):
            raise RuntimeError("__name__ boom")

    class BoomError(Exception, metaclass=BoomMeta):
        pass

    class Evil:
        @property
        def __class__(self):
            raise BoomError("x")

    text, meta = compress.render(Evil(), preset="snyk", store=False)
    assert isinstance(text, str) and meta["loss_reason"] == "renderer_error"
