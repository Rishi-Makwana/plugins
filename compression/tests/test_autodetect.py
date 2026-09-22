"""Autodetect: used only when no preset was given and nothing sniffed."""
import json

import compress
from compress.autodetect import LEXICONS, detect, find_records
from conftest import load


def test_lexicons_match_compress_check_byte_for_byte():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    source = (root / "compress_check.py").read_text()
    block = re.search(r"^LEXICONS = \[.*?^\]", source, re.S | re.M).group(0)
    ns: dict = {}
    exec(block, ns)
    assert ns["LEXICONS"] == LEXICONS


def test_finds_the_largest_record_array():
    data = {"small": [{"a": 1}] * 3, "big": {"rows": [{"a": i} for i in range(20)]}}
    path, records = find_records(data)
    assert path == "big.rows" and len(records) == 20


def test_detect_picks_the_log_lexicon_for_level():
    data = json.loads(load("unknown-json.json"))
    preset = detect(data)
    assert preset["rank"]["path"] == "level"
    assert preset["rows_path"] == ["events"]
    assert preset["alert"][0]["in"] == ["error", "fatal", "panic"]


def test_autodetected_render_surfaces_every_tier_zero_record():
    text, meta = compress.render(load("unknown-json.json"), max_bytes=12288, store=False)
    assert meta["detected"] is True
    assert meta["alert_count"] == 24
    assert meta["alert_recall"] == 1.0
    assert meta["shown_by_tier"]["error"] == meta["by_tier"]["error"] == 24


def test_detect_on_a_shapeless_payload_does_not_raise():
    preset = detect({"a": 1})
    assert preset["rows_path"] == ["$"]
    text, meta = compress.render({"a": 1, "b": "c"}, store=False)
    assert meta["total"] == 0
