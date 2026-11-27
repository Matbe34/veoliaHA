"""Translations stay in step with `strings.json`."""
import json
import re
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


def test_english_sensor_names_match_the_names_entities_had_before_translations():
    # Once en.json names a translation_key, it replaces the description's
    # `name`, so any difference would rename existing users' entities.
    source = (DIR / "sensor.py").read_text(encoding="utf-8")
    described = dict(re.findall(r'translation_key="([^"]+)",\s*name="([^"]+)"', source))
    assert described
    names = _load(DIR / "translations" / "en.json")["entity"]["sensor"]
    assert {key: names[key]["name"] for key in described} == described
    assert _load(DIR / "strings.json")["entity"]["sensor"] == names
