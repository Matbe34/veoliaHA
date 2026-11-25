"""Translations stay in step with `strings.json`."""
import json
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parent.parent / "custom_components" / "veolia_water"


def _keys(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _keys(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip(".")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", ["en", "es"])
def test_translation_has_exactly_the_keys_of_strings_json(lang):
    source = set(_keys(_load(DIR / "strings.json")))
    translated = set(_keys(_load(DIR / "translations" / f"{lang}.json")))
    assert translated == source
