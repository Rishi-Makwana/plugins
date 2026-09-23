"""Retrieval: the footer promises a way back in, and this is it."""
import json
import shlex

import pytest

import importlib

import compress
from compress.reduce import bytelen
from compress import store as store_mod
from compress.fetch import (EXIT_ERROR, EXIT_NO_MATCH, EXIT_OK, EXIT_UNKNOWN,
                            fetch)
from conftest import all_fixtures, expect, load

# `compress.fetch` is the public function (6); the module lives in sys.modules.
fetch_mod = importlib.import_module("compress.fetch")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """Every fixture rendered once into one database."""
    db = str(tmp_path_factory.mktemp("fetchdb") / "compress.db")
    out = {}
    for name, exp in all_fixtures():
        text, meta = compress.render(load(name), preset=exp["preset"],
                                     max_bytes=12288, db=db, source=name)
        out[name] = (text, meta)
    return db, out


# 17 -- round trip, untruncated
def test_ids_round_trip_untruncated(rendered):
    db, out = rendered
    _, meta = out["deep-trunc.json"]
    token = expect("deep-trunc.json")["search_token"]
    conn = store_mod.connect(db)
    try:
        ident = conn.execute('SELECT ident FROM "rows" WHERE render_id = ? LIMIT 1',
                             (meta["render_id"],)).fetchone()[0]
    finally:
        conn.close()
    first_id = ident.split("\x1f")[0]

    text, m = fetch(meta["render_id"], ids=[first_id], full=True, db=db,
                    max_bytes=65536)
    assert m["exit"] == EXIT_OK and m["matched"] == 1
    assert token in text                      # char 700 of a 900-char title

    cut, _ = fetch(meta["render_id"], ids=[first_id], full=False, db=db,
                   max_bytes=65536)
    assert "…" in cut and token not in cut


# 18 -- hidden completeness, exactly, on every fixture
@pytest.mark.parametrize("name,exp", all_fixtures())
def test_hidden_count_equals_total_minus_shown(rendered, name, exp):
    db, out = rendered
    _, meta = out[name]
    if meta["total"] == 0:
        pytest.skip("no rows stored")
    _, m = fetch(meta["render_id"], hidden=True, db=db, max_bytes=2048)
    assert m.get("matched", 0) == meta["total"] - meta["shown"]


# 19 -- where on a dotted key
def test_where_on_a_dotted_key(rendered):
    db, out = rendered
    _, meta = out["dependabot-6.json"]
    text, m = fetch(meta["render_id"], db=db,
                    where={"security_advisory.severity": "critical"})
    assert m["exit"] == EXIT_OK and m["matched"] == 2
    assert '"security_advisory.severity":"critical"' in text


# 20 -- where as a predicate dict
def test_where_predicate_dict_is_numeric(rendered):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    text, m = fetch(meta["render_id"], db=db, where={"cvssScore": {"gte": 9.0}},
                    max_bytes=8192)
    assert m["matched"] == 2
    for line in text.splitlines():
        if line.startswith("{"):
            assert json.loads(line)["cvssScore"] >= 9.0


# 21 -- search finds a word the slice never showed
def test_search_finds_a_truncated_word(rendered):
    db, out = rendered
    _, meta = out["deep-trunc.json"]
    token = expect("deep-trunc.json")["search_token"]
    assert token not in out["deep-trunc.json"][0]        # never displayed
    _, m = fetch(meta["render_id"], search=token, db=db, max_bytes=4096)
    assert m["matched"] == 40


# 22 -- search sanitising
@pytest.mark.parametrize("term", ["CVE-2024-1234", "a OR", 'NOT "x', "AND", "*"])
def test_search_never_trips_on_fts_syntax(rendered, term):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    text, m = fetch(meta["render_id"], search=term, db=db, max_bytes=4096)
    assert m.get("exit") in (EXIT_OK, EXIT_NO_MATCH)
    assert "error" not in m


def test_sanitise_quotes_bare_terms():
    assert fetch_mod.sanitise_query("CVE-2024-1234") == '"CVE-2024-1234"'
    assert fetch_mod.sanitise_query("prototype pollution") == '"prototype" "pollution"'
    assert fetch_mod.sanitise_query('"already quoted"') == '"already quoted"'


# 23 -- bm25 puts the denser row first
def test_bm25_ranks_repetition_higher(tmp_path):
    db = str(tmp_path / "bm25.db")
    payload = {"ok": False, "packageManager": "npm", "vulnerabilities": [
        {"id": "ONCE", "packageName": "a", "version": "1", "severity": "low",
         "title": "pollution of the filler filler filler"},
        {"id": "THRICE", "packageName": "b", "version": "1", "severity": "low",
         "title": "pollution pollution pollution"}]}
    _, meta = compress.render(payload, preset="snyk", db=db)
    text, m = fetch(meta["render_id"], search="pollution", db=db, max_bytes=65536)
    body = [line for line in text.splitlines() if line.startswith("{")]
    assert m["matched"] == 2
    assert json.loads(body[0])["id"] == "THRICE"


# 24 -- a fetch is not a second context bomb
def test_fetch_respects_the_budget(tmp_path):
    db = str(tmp_path / "big.db")
    payload = {"ok": False, "packageManager": "npm", "vulnerabilities": [
        {"id": f"V-{i:04d}", "packageName": f"p{i % 40}", "version": "1",
         "severity": "critical" if i < 6 else "medium", "cvssScore": 9.9 if i < 6 else 4.0,
         "title": "x" * 120} for i in range(400)]}
    _, meta = compress.render(payload, preset="snyk", db=db)
    text, m = fetch(meta["render_id"], db=db, where={"packageName": "p0"},
                    max_bytes=4096)
    assert bytelen(text) <= 4096
    assert m["matched"] == 10
    body = [line for line in text.splitlines() if line.startswith("{")]
    assert json.loads(body[0])["severity"] == "critical"     # alerts still first


# 25 -- a typo must not cost a second round trip
def test_zero_match_prints_the_distribution(rendered):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    text, m = fetch(meta["render_id"], db=db, where={"severity": "kritical"})
    assert m["exit"] == EXIT_NO_MATCH
    assert "matched 0 of 200" in text
    assert "severity values present:" in text
    assert "critical 2" in text and "low 129" in text


def test_zero_match_on_an_unknown_field_names_the_keys(rendered):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    text, m = fetch(meta["render_id"], db=db, where={"nosuchfield": "x"})
    assert m["exit"] == EXIT_NO_MATCH
    assert "no field `nosuchfield`" in text
    assert "packageName" in text and "cvssScore" in text


# 26 -- prefix resolution
def test_unique_prefix_resolves(rendered):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    _, m = fetch(meta["render_id"][:6], db=db)
    assert m["exit"] == EXIT_OK and m["render_id"] == meta["render_id"]


def test_ambiguous_prefix_exits_two_and_lists_candidates(tmp_path):
    db = str(tmp_path / "amb.db")
    conn = store_mod.connect(db)
    try:
        for suffix in ("0001", "0002"):
            conn.execute("INSERT INTO renders (render_id, preset, detected,"
                         " created_at, raw_total, total, shown, meta)"
                         " VALUES (?,?,?,?,?,?,?,?)",
                         (f"abc12300{suffix}", "snyk", 0, 1.0, 1, 1, 1, "{}"))
    finally:
        conn.close()
    text, m = fetch("abc123", db=db)
    assert m["exit"] == EXIT_UNKNOWN
    assert "ambiguous prefix abc123" in text
    assert "abc123" in text


def test_unknown_render_lists_the_recent_ones(rendered):
    db, _ = rendered
    text, m = fetch("ffffffff", db=db)
    assert m["exit"] == EXIT_UNKNOWN
    assert "unknown render ffffffff" in text
    assert "candidates, newest first:" in text


def test_summary_only_when_no_predicate_is_given(rendered):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    text, m = fetch(meta["render_id"], db=db)
    assert m["exit"] == EXIT_OK
    assert not [line for line in text.splitlines() if line.startswith("{")]
    assert "· summary" in text and "critical" in text


# 35 -- the footer's worked example is real
@pytest.mark.parametrize("name,exp", all_fixtures())
def test_footer_fetch_example_runs_verbatim(rendered, name, exp):
    db, out = rendered
    text, meta = out[name]
    line = next((l for l in text.splitlines() if l.startswith("fetch: compress_fetch")),
                None)
    if line is None:
        assert meta["withheld"] == 0 or not meta["stored"]
        return
    arms = [a.strip() for a in line[len("fetch: "):].split("·") if a.strip()]
    head = shlex.split(arms[0])
    assert head[0] == "compress_fetch"
    base = head[1]                                  # the render id, shared by all arms
    for arm in arms:
        argv = shlex.split(arm)
        if argv and argv[0] == "compress_fetch":
            argv = argv[2:]                         # drop the command and the id
        rid = base
        args = argv
        kwargs = {"db": db, "max_bytes": 4096}
        i = 0
        while i < len(args):
            if args[i] == "--where":
                key, _, value = args[i + 1].partition("=")
                kwargs.setdefault("where", {})[key] = value
                i += 2
            elif args[i] == "--hidden":
                kwargs["hidden"] = True
                i += 1
            else:
                pytest.fail(f"{name}: unhandled arm {arm!r}")
        _, m = fetch(rid, **kwargs)
        assert m["exit"] == EXIT_OK, f"{name}: `{arm.strip()}` returned no rows"
        assert m["matched"] > 0, f"{name}: `{arm.strip()}` matched nothing"


def test_cli_exit_codes(rendered, capsys):
    db, out = rendered
    _, meta = out["snyk-200.json"]
    rid = meta["render_id"][:6]
    assert fetch_mod.main([rid, "--where", "severity=high", "--db", db]) == EXIT_OK
    assert fetch_mod.main([rid, "--where", "severity=nope", "--db", db]) == EXIT_NO_MATCH
    assert fetch_mod.main(["ffffffff", "--db", db]) == EXIT_UNKNOWN
    assert fetch_mod.main(["--db", db]) == EXIT_OK
    capsys.readouterr()
    assert fetch_mod.main([rid, "--count", "--alerts", "--db", db]) == EXIT_OK
    assert "matched 11" in capsys.readouterr().out


def test_cli_gc_runs(rendered, capsys):
    db, _ = rendered
    assert fetch_mod.main(["--gc", "--days", "7", "--keep", "200", "--db", db]) == EXIT_OK
    assert "gc:" in capsys.readouterr().out


def test_fetch_never_raises(tmp_path):
    text, meta = fetch("nope", db=str(tmp_path / "missing.db"))
    assert isinstance(text, str) and meta["exit"] in (EXIT_UNKNOWN, EXIT_ERROR)
    text, meta = fetch(None, db=str(tmp_path / "missing2.db"))
    assert "no renders stored" in text


def test_the_package_attribute_stays_the_function():
    """Loading compress.fetch must not leave compress.fetch bound to the module."""
    importlib.import_module("compress.fetch")
    assert callable(compress.fetch) and not hasattr(compress.fetch, "__path__")
    assert compress.fetch is fetch
