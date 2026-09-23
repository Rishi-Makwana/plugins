"""Shared fixture access. Every test writes to tmp dirs, never /tool-results."""
from __future__ import annotations

import json
import os
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _isolated_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("ldm-compress")
    os.environ["LDM_COMPRESS_DIR"] = str(root)
    os.environ["LDM_COMPRESS_DB"] = str(root / "compress.db")
    yield root


def fixture_names():
    return sorted(p.name for p in FIXTURES.iterdir()
                  if not p.name.endswith(".expect.json"))


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def expect(name: str) -> dict:
    return json.loads((FIXTURES / (name.rsplit(".", 1)[0] + ".expect.json")).read_text())


def all_fixtures():
    return [(n, expect(n)) for n in fixture_names()]


RULE = "\u2500" * 60


def footer_of(text: str) -> str:
    """Everything from the opening rule onward. The body never contains a rule."""
    i = text.find(RULE)
    return text[i:] if i >= 0 else ""
