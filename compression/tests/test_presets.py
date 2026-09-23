"""Preset table and sniffing."""
import json

import pytest

import compress
from compress.presets import PRESETS, sniff
from conftest import all_fixtures, load

REQUIRED = ("name", "fields", "rank")


def test_nine_presets_are_registered():
    assert set(PRESETS) == {"sarif", "snyk", "sonarqube", "dependabot", "trivy",
                            "gh_runs", "pytest_json", "junit", "logs"}


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_has_the_required_keys(name):
    preset = PRESETS[name]
    for key in REQUIRED:
        assert key in preset, f"{name} missing {key}"
    if preset["kind"] == "records":
        assert preset.get("rows_path"), f"{name} needs rows_path"
        assert preset["rank"].get("path") in preset["fields"], \
            f"{name}: rank field must be projected so --where can reach it"
    else:
        assert preset["rank"].get("regex_order"), f"{name} needs rank.regex_order"


def test_sarif_is_first_so_it_wins_ties():
    from compress.presets import _ORDERED
    assert _ORDERED[0]["name"] == "sarif"


# 16 -- each fixture resolves to its preset; unknown resolves to None
@pytest.mark.parametrize("name,exp", all_fixtures())
def test_sniff_resolves_each_fixture(name, exp):
    if name.endswith((".xml", ".log")):
        pytest.skip("not JSON; the adapter decides")
    data = json.loads(load(name))
    assert sniff(data) == exp["preset"]


def test_sniff_returns_none_for_an_unknown_shape():
    assert sniff({"totally": "unknown", "rows": [1, 2, 3]}) is None
    assert sniff([]) is None
    assert sniff("a string") is None


def test_presets_are_exported_from_the_package():
    assert compress.PRESETS is PRESETS
    assert compress.sniff({"tests": [], "summary": {}}) == "pytest_json"
