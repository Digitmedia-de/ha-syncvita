# Mitschnitt API 1.6 – Abgleich mit den Annahmen des Plans

Aufgenommen am 2026-10-10 gegen 15:57 UTC mit `scripts/capture_fixtures.py`, anonymisiert (Zahlen skaliert, `identifier` ersetzt).

| Annahme | Ergebnis |
| --- | --- |
| `/health-export/metrics`: `enabled`, `last_upload_at`, `metrics[]` mit `metric`, `kind`, `unit`, `cumulative`, `count`, `first_date`, `last_date`, `latest.value/date/time` | bestätigt |
| `last_upload_at` im Format `YYYY-MM-DD HH:MM:SS`, UTC | Format bestätigt; Wert `15:44:11` lag 13 Minuten vor dem Mitschnitt (15:57 UTC), passt also zu UTC. Ortszeit hätte einen Upload vier Stunden vorher bedeutet. Als UTC behandelt. |
| Schlaf: `data.nights[]` | Schlüssel `nights` bestätigt, zusätzlich `aggregation: "sleep"` und `unit: "min"`. Das Konto hatte keine Nächte, Feldnamen einer Nacht daher **nicht** bestätigt. |
| Workouts: `data.workouts[]` nach Schema `HealthExportWorkout` | Schlüssel `workouts` bestätigt, zusätzlich `aggregation` und `unit`. Das Konto hatte keine Workouts, Feldnamen laut OpenAPI-Schema. |
| Rate-Limit-Header auch bei HTTP 200 | bestätigt für `/health-export/*`; `/stats` liefert keine Rate-Limit-Header (dafür `data.rate_limit` im Body). |
| 401-Antwort `error.code == "UNAUTHORIZED"` | **abweichend**: `INVALID_API_KEY` mit Meldung „Invalid or missing API key“. Der Client wertet nur den Status aus, daher unkritisch. |
