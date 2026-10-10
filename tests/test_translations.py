"""Translation files must stay in sync."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "syncvita"


def _load(name: str) -> dict[str, Any]:
    return json.loads((INTEGRATION / name).read_text(encoding="utf-8"))


def _keys(data: Any, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    keys: set[str] = set()
    for key, value in data.items():
        keys |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return keys


def test_english_matches_strings() -> None:
    assert _load("translations/en.json") == _load("strings.json")


def test_german_has_same_keys() -> None:
    assert _keys(_load("translations/de.json")) == _keys(_load("strings.json"))
