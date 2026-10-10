"""Consistency checks for manifest.json, hacs.json and pyproject.toml."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "syncvita"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_has_hacs_fields() -> None:
    manifest = _json(INTEGRATION / "manifest.json")
    for key in (
        "domain",
        "documentation",
        "issue_tracker",
        "codeowners",
        "name",
        "version",
    ):
        assert manifest[key], key
    assert manifest["domain"] == "syncvita"
    assert manifest["integration_type"] == "service"
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []


def test_versions_match() -> None:
    manifest = _json(INTEGRATION / "manifest.json")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["version"] == pyproject["project"]["version"]


def test_hacs_json() -> None:
    assert _json(ROOT / "hacs.json") == {
        "name": "Syncvita",
        "homeassistant": "2026.3.0",
        "render_readme": True,
    }
