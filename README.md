# Syncvita für Home Assistant

Diese Integration sendet neue Messwerte aus Home Assistant (z. B. Waage, Pulsoximeter, Trinkflasche) automatisch an [Syncvita](https://syncvita.de) und zeigt die Apple-Health-Daten, die die Syncvita-iPhone-App hochlädt, als Entitäten in Home Assistant an. Die Einrichtung läuft komplett über die Oberfläche, ein Eintrag pro Person.

## Funktionen

- **Senden:** Gewicht, Körperfett, Herzfrequenz, Blutsauerstoff, Blutzucker und Trinkmenge, jeweils genau einmal, mit dem Messzeitpunkt aus Home Assistant. Bei Netzproblemen landen Werte in einer Warteschlange und gehen später gesammelt raus.
- **Lesen:** Status des Apple-Health-Exports, letzte Werte je Typ (z. B. Ruhepuls, HRV, VO2max), Schritte heute, Schlaf der letzten Nacht und Workouts.
- **Ereignisse:** `syncvita_workout_finished` und `syncvita_sleep_recorded` für Automationen, zusätzlich als Ereignis-Entitäten.
- **Aktionen:** `syncvita.send_value` und `syncvita.backfill`.

## Voraussetzungen

- Home Assistant 2026.3 oder neuer
- Ein Syncvita-API-Key pro Person
- Für den Lese-Teil: in der Syncvita-iPhone-App freigegebener Apple-Health-Export

## Installation über HACS

1. HACS öffnen → Menü oben rechts → **Benutzerdefinierte Repositories**.
2. URL `https://github.com/Digitmedia-de/ha-syncvita` eintragen, Typ **Integration**, hinzufügen.
3. „Syncvita“ suchen, herunterladen und Home Assistant neu starten.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Syncvita**.
2. Name der Person und API-Key eingeben. Der Key wird über `GET /stats` geprüft.
3. Optional pro Messart einen Sensor wählen. Bleiben alle Felder leer, sendest du Werte nur über die Aktion `syncvita.send_value` (z. B. aus Automatisierungen). Sensoren mit falscher Einheit werden abgelehnt; Syncvita-eigene Sensoren werden nicht angeboten, damit keine Kreisläufe entstehen.

Für eine zweite Person die Integration einfach noch einmal hinzufügen.

## Optionen

Unter **Konfigurieren** gibt es drei Bereiche:

- **Sensoren:** Zuordnung ändern.
- **Senden:** Fehlerbenachrichtigung an/aus (Standard an) und Höchstalter für Werte in der Warteschlange (Standard 72 Stunden).
- **Apple-Health-Export:** Lesen an/aus, Abfrageintervall 10–60 Minuten (Standard 15), Schlaf und Workouts an/aus.

Welche Typ-Sensoren aktiv sind, steuerst du wie gewohnt über **Entitäten → aktivieren/deaktivieren**. Standardmäßig aktiv sind Ruhepuls, HRV, Sauerstoffsättigung, Atemfrequenz, VO2max und Schritte heute. Die Sensoren für Schlaf und Workouts schaltest du über die Optionen ein und aus.

## Entitäten

Alle Entitäten hängen am Gerät „Syncvita <Name>“.

| Entität | Inhalt |
| --- | --- |
| Letzte Übertragung | Zeitpunkt des letzten erfolgreichen Sendens |
| Gesendete Werte | Zähler seit Einrichtung |
| Warteschlange | offene Werte, Attribut `oldest` |
| Letzter Fehler | Kurztext, Attribut `http_status` |
| Verbleibende API-Anfragen | standardmäßig deaktiviert |
| Warteschlange jetzt senden | Button |
| Export aktiv | an/aus, Attribut `released_types` |
| Letzter Upload aus Apple Health | Zeitpunkt, Attribut `age_minutes` |
| Gespeicherte Exportwerte | Summe, Attribut `counts` |
| `<Typ>` / `<Typ> heute` | je Apple-Health-Typ mit Werten, Attribute `measured_at` und `stale` (nach 48 Stunden ohne neuen Wert) |
| Schlaf letzte Nacht | Stunden, Attribute `night`, `bedtime`, `wake_time`, `stages_minutes` |
| Letztes Workout / Workouts heute | Aktivität mit Start, Ende, Dauer, kcal, Distanz, Puls / Anzahl |
| Workout / Schlaf | Ereignis-Entitäten zu den beiden Ereignissen |

## Ereignisse

```yaml
automation:
  - alias: Workout fertig
    triggers:
      - trigger: event
        event_type: syncvita_workout_finished
    actions:
      - action: notify.mobile_app_iphone
        data:
          message: >
            {{ trigger.event.data.activity_type }}:
            {{ trigger.event.data.duration_minutes }} min,
            {{ trigger.event.data.energy_kcal }} kcal
```

`syncvita_sleep_recorded` liefert `night`, `duration_hours`, `bedtime`, `wake_time` und `stages_minutes`. Beide Ereignisse enthalten `config_entry_id`.

## Aktionen

```yaml
action: syncvita.send_value
data:
  config_entry_id: 0123456789abcdef0123456789abcdef
  measure: weight
  value: 80.4
  measured_at: "2026-10-10 07:15:00"
```

```yaml
action: syncvita.backfill
data:
  config_entry_id: 0123456789abcdef0123456789abcdef
  start: "2026-10-01"
  measures: [weight, body_fat]
response_variable: ergebnis
```

`backfill` liest die Historie der zugeordneten Sensoren aus dem Recorder, wendet dieselben Regeln wie beim Live-Senden an, überspringt bereits Gesendetes und gibt `sent`, `skipped` und `failed` zurück.

## Datenschutz und Recorder

Die gelesenen Gesundheitswerte landen wie alle Zustände im Home-Assistant-Recorder. Das gilt auch für die Ereignis-Entitäten (Workout, Schlaf) und den Binärsensor „Export aktiv“. Zusätzlich speichert der Recorder die Bus-Ereignisse `syncvita_workout_finished` und `syncvita_sleep_recorded` samt Inhalt in seiner `events`-Tabelle. Wer das nicht möchte, schließt Entitäten und Ereignistypen aus:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.syncvita_*
      - event.syncvita_*
      - binary_sensor.syncvita_*
    event_types:
      - syncvita_workout_finished
      - syncvita_sleep_recorded
```

Die Globs passen, solange die Entity-IDs mit `syncvita_` beginnen. Wer sie umbenannt hat, ergänzt die eigenen IDs unter `entities:`.

Diagnosedaten enthalten nur Typnamen, Anzahlen und Zeitpunkte, keine Messwerte und keinen API-Key.

## Grenzen

- Misst die Waage zweimal exakt denselben Wert, ändert sich der Zustand in Home Assistant nicht. Es gibt dann kein Ereignis und nichts wird gesendet.
- Messungen, die eintreffen, während Home Assistant nicht läuft, werden nicht automatisch gesendet. `syncvita.backfill` holt sie nach.
- Als Messzeitpunkt gilt `last_changed`, also der Moment, in dem Home Assistant den Wert gesehen hat. Bei Cloud-Integrationen wie Withings kann das einige Minuten nach der eigentlichen Messung sein.
- Schritte (`/steps`) werden nicht gesendet, weil die API dafür Zeitfenster statt Einzelwerte erwartet.

## Abweichungen von der Spezifikation

1. „<Typ> heute“ nutzt `latest.value` aus `/health-export/metrics` (laut API die Summe des letzten Tags). `/daily` wird nur für Schlaf und Workouts abgefragt.
2. Die Auswahl der Typ-Sensoren läuft über die Entity-Registry statt über eine Mehrfachauswahl im Options-Flow.
3. Das Abfrageintervall ist auf 10–60 Minuten begrenzt, `/settings` wird stündlich gelesen. So bleibt der Lese-Teil sicher unter 30 Anfragen pro Stunde.
4. Der erste Export-Abruf blockiert das Setup nicht (`async_refresh` statt `async_config_entry_first_refresh`), damit der Sende-Teil auch bei Netzproblemen sofort Werte einreiht.
5. Blutdruck, Stehstunden und Achtsamkeit bekommen in v0.1.0 keine eigenen Sensoren.
6. Der Hinweis auf Sensoren, die schon an eine andere Person senden, ist ein eigener Bestätigungsschritt.
7. Der Reparatur-Dialog für fehlende Sensoren fragt den Ersatz direkt ab, statt den Options-Flow zu öffnen.
8. Workouts werden für die letzten 7 Tage gelesen, damit „Letztes Workout“ auch nach Mitternacht gefüllt bleibt.
9. Fehlerbenachrichtigungen sind nur auf Deutsch.
10. Wird bei der erneuten Anmeldung oder beim Ändern des Kontos ein neuer Key eingegeben, übernimmt der Eintrag dessen Kennung. Ein Key, der schon zu einem anderen Eintrag gehört, wird abgelehnt.

## Fehlersuche

- Diagnose herunterladen: **Geräte & Dienste → Syncvita → ⋮ → Diagnose herunterladen**.
- Ausführliches Log:

  ```yaml
  logger:
    logs:
      custom_components.syncvita: debug
  ```

- „Letzter Fehler“ und „Warteschlange“ zeigen, ob gerade etwas hängt. Der Button „Warteschlange jetzt senden“ stößt das Senden sofort an.
- Bei ungültigem Key startet Home Assistant automatisch die erneute Anmeldung.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
