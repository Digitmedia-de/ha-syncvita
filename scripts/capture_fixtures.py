#!/usr/bin/env python3
"""Capture Syncvita API 1.6 responses as anonymized test fixtures.

Usage:
    SYNCVITA_API_KEY=... python3 -I scripts/capture_fixtures.py

Raw responses go to tests/fixtures/_raw/ (git-ignored), anonymized copies to
tests/fixtures/real/. Numbers that could be health values are scaled by a
seeded random factor, UUIDs and source/device names are replaced.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import random
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "https://api.syncvita.de/v1"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
RAW = FIXTURES / "_raw"
REAL = FIXTURES / "real"
KEEP_NUMBERS = {"count", "index", "id", "history_days", "activity_type_raw", "days"}
NAME_KEYS = {"source", "source_name", "device", "device_name", "name"}
ID_KEYS = {"identifier"}
RATE_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
RNG = random.Random(4711)
NAMESPACE = uuid.UUID("6f1c3a4e-0000-4000-8000-000000000000")


def request(path: str, params: dict[str, str] | None, key: str) -> dict[str, Any]:
    """GET a path and return status, rate-limit headers and JSON body."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "X-API-Version": "1.6",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status, headers, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        status, headers, raw = err.code, err.headers, err.read()
    body = json.loads(raw) if raw else None
    return {
        "status": status,
        "headers": {h: headers.get(h) for h in RATE_HEADERS if headers.get(h)},
        "body": body,
    }


def anonymize(value: Any, key: str = "") -> Any:
    """Return an anonymized copy of a JSON value."""
    if isinstance(value, dict):
        return {k: anonymize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [anonymize(v, key) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if key in KEEP_NUMBERS:
            return value
        scaled = value * RNG.uniform(0.85, 1.15)
        return round(scaled) if isinstance(value, int) else round(scaled, 2)
    if isinstance(value, str):
        if key == "uuid":
            return str(uuid.uuid5(NAMESPACE, value))
        if key in NAME_KEYS:
            return "Device"
        if key in ID_KEYS:
            return "00000000"
    return value


def main() -> int:
    """Capture all fixtures."""
    key = os.environ.get("SYNCVITA_API_KEY")
    if not key:
        print("SYNCVITA_API_KEY fehlt", file=sys.stderr)
        return 1
    today = dt.date.today()

    def daily(metric: str, days: int) -> dict[str, str]:
        start = today - dt.timedelta(days=days - 1)
        return {"metric": metric, "from": start.isoformat(), "to": today.isoformat()}

    calls: dict[str, tuple[str, dict[str, str] | None, str]] = {
        "stats": ("/stats", None, key),
        "health_export_settings": ("/health-export/settings", None, key),
        "health_export_metrics": ("/health-export/metrics", None, key),
        "health_export_daily_sleep": (
            "/health-export/daily",
            daily("sleep_analysis", 14),
            key,
        ),
        "health_export_daily_workouts": (
            "/health-export/daily",
            daily("workouts", 30),
            key,
        ),
        "health_export_daily_step_count": (
            "/health-export/daily",
            daily("step_count", 2),
            key,
        ),
        "health_export_daily_heart_rate": (
            "/health-export/daily",
            daily("heart_rate", 2),
            key,
        ),
        "unauthorized": ("/health-export/metrics", None, "invalid-key"),
    }
    RAW.mkdir(parents=True, exist_ok=True)
    REAL.mkdir(parents=True, exist_ok=True)
    for name, (path, params, api_key) in calls.items():
        result = request(path, params, api_key)
        (RAW / f"{name}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False)
        )
        clean = {**result, "body": anonymize(result["body"])}
        (REAL / f"{name}.json").write_text(
            json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
        )
        data = (result["body"] or {}).get("data")
        shape = sorted(data) if isinstance(data, dict) else type(data).__name__
        print(f"{name}: HTTP {result['status']} {result['headers']} data={shape}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
