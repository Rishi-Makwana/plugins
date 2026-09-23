from compress.paths import get

OBJ = {"a": {"b": 1}, "l": [{"b": 2}, {"b": 3}], "n": [{"b": [1, 2]}, {"b": [3]}],
       "flat.key": "literal"}


def test_dotted():
    assert get(OBJ, "a.b") == 1


def test_index():
    assert get(OBJ, "l[0].b") == 2
    assert get(OBJ, "l[1].b") == 3


def test_each():
    assert get(OBJ, "l[].b") == [2, 3]


def test_each_flattens_one_level():
    assert get(OBJ, "n[].b") == [1, 2, 3]


def test_root():
    assert get(OBJ, "$") is OBJ


def test_miss_returns_default():
    assert get(OBJ, "nope", "D") == "D"
    assert get(OBJ, "a.b.c", "D") == "D"
    assert get(OBJ, "l[9].b", "D") == "D"


def test_non_container_roots_return_default():
    for root in (None, "a string", 3, 4.5, True):
        assert get(root, "a.b", "D") == "D"


def test_literal_dotted_key_wins():
    # projected records key on the dotted path string
    assert get(OBJ, "flat.key") == "literal"


def test_never_raises():
    assert get(OBJ, None, "D") == "D"
    get(OBJ, "a[[[", "D")        # malformed path: lenient, but must not raise
    get([1, 2, 3], "a.b", "D")
