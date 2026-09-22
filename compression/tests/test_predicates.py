from compress.predicates import evaluate


def test_in_is_case_insensitive_and_stripped():
    assert evaluate({"s": "  HIGH "}, {"path": "s", "in": ["high"]}, 9)
    assert not evaluate({"s": "low"}, {"path": "s", "in": ["high"]}, 9)


def test_eq():
    assert evaluate({"t": "VULNERABILITY"}, {"path": "t", "eq": "VULNERABILITY"}, 9)
    assert not evaluate({"t": "BUG"}, {"path": "t", "eq": "VULNERABILITY"}, 9)


def test_gte_and_lte():
    assert evaluate({"s": 9.4}, {"path": "s", "gte": 9.0}, 9)
    assert evaluate({"s": "7.5"}, {"path": "s", "gte": 7.0}, 9)
    assert evaluate({"s": 1}, {"path": "s", "lte": 2}, 9)


def test_gte_against_non_numeric_is_false():
    assert not evaluate({"s": "abc"}, {"path": "s", "gte": 1}, 9)
    assert not evaluate({}, {"path": "s", "gte": 1}, 9)


def test_matches():
    assert evaluate({"u": "sarif-2.1.0"}, {"path": "u", "matches": "(?i)SARIF"}, 9)


def test_malformed_regex_is_false():
    assert not evaluate({"u": "x"}, {"path": "u", "matches": "["}, 9)


def test_exists():
    assert evaluate({"k": 0}, {"path": "k", "exists": True}, 9)
    assert evaluate({}, {"path": "k", "exists": False}, 9)
    assert not evaluate({}, {"path": "k", "exists": True}, 9)


def test_rank_tier_ignores_path():
    assert evaluate({}, {"rank_tier": 0}, 0)
    assert not evaluate({}, {"rank_tier": 0}, 1)


def test_unknown_key_is_false():
    assert not evaluate({"a": 1}, {"path": "a", "zzz": 1}, 0)
    assert not evaluate({"a": 1}, {}, 0)
