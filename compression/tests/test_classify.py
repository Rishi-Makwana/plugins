from compress.classify import is_alert, sort_key, tier_of

RANK = {"path": "severity", "order": ["critical", "high", "medium", "low"], "default": 3}


def test_known_value():
    assert tier_of({"severity": "High"}, RANK) == (1, "high")


def test_unknown_value_falls_to_default_and_other():
    assert tier_of({"severity": "spicy"}, RANK) == (3, "other")
    assert tier_of({}, RANK) == (3, "other")


def test_alert_via_predicate():
    cfg = [{"path": "exploitMaturity", "in": ["mature", "functional"]}]
    assert is_alert({"exploitMaturity": "mature"}, cfg, 3)
    assert not is_alert({"exploitMaturity": "none"}, cfg, 3)


def test_alert_falls_back_to_tier_zero_without_config():
    assert is_alert({}, None, 0)
    assert not is_alert({}, [], 1)


def test_sort_key_orders_by_tier_then_score_then_index():
    score = {"path": "cvssScore", "desc": True}
    a = sort_key({"cvssScore": 9.8}, 0, score, 5)
    b = sort_key({"cvssScore": 4.0}, 0, score, 1)
    assert a < b


def test_sort_key_is_stable_on_equal_keys():
    rows = [({"cvssScore": 5.0}, i) for i in range(5)]
    keys = [sort_key(r, 1, {"path": "cvssScore", "desc": True}, i) for r, i in rows]
    assert keys == sorted(keys)


def test_sort_key_survives_non_numeric_score():
    assert sort_key({"cvssScore": "n/a"}, 0, {"path": "cvssScore", "desc": True}, 0) \
        == (0, 0.0, 0)
