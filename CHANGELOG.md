# Changelog

Alle nennenswerten Änderungen an dieser Integration. Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen folgen [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.1.3] – 2026-10-10

### Hinzugefügt

- Sensoren für die in Syncvita gespeicherten Werte – auch ohne Apple-Health-Export: Gewicht, Körperfett, Herzfrequenz, Blutsauerstoff, Blutzucker (jeweils letzter Wert mit Messzeit), Schritte heute und Trinkmenge heute.

### Geändert

- Sensoren aus dem Apple-Health-Export tragen den Zusatz „(Apple Health)“, z. B. „Gewicht (Apple Health)“. Bestehende Entity-IDs bleiben unverändert.

## [0.1.2] – 2026-10-10

### Geändert

- Sensoren für Apple-Health-Werte heißen nur noch nach dem Typ, z. B. „Ruhepuls“ statt „Ruhepuls letzter Wert“. Bestehende Entity-IDs bleiben unverändert.

## [0.1.1] – 2026-10-10

### Geändert

- Die Sensorauswahl ist optional – bei der Einrichtung, in den Optionen und im Reparaturdialog. Ohne zugeordnete Sensoren gehen Werte nur über die Aktion `syncvita.send_value` an Syncvita, z. B. aus Automatisierungen.

## [0.1.0] – 2026-10-10

### Hinzugefügt

- Einrichtung über die Oberfläche, ein Eintrag pro Person, mit Prüfung des API-Keys, Reauth und Reconfigure.
- Senden von Gewicht, Körperfett, Herzfrequenz, Blutsauerstoff, Blutzucker und Trinkmenge aus zugeordneten Sensoren, mit Einheiten-Umrechnung, Bereichsprüfung, Deduplizierung und Warteschlange bei Netzproblemen.
- Lesen des Apple-Health-Exports: Exportstatus, letzte Werte je Typ, Schritte heute, Schlaf und Workouts.
- Ereignisse `syncvita_workout_finished` und `syncvita_sleep_recorded`, auch als Ereignis-Entitäten.
- Aktionen `syncvita.send_value` und `syncvita.backfill`.
- Diagnose-Sensoren, Button zum Abarbeiten der Warteschlange, Diagnosedaten ohne Messwerte und Reparaturhinweise für fehlende Sensoren.

[Unreleased]: https://github.com/Digitmedia-de/ha-syncvita/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/Digitmedia-de/ha-syncvita/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Digitmedia-de/ha-syncvita/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Digitmedia-de/ha-syncvita/releases/tag/v0.1.1
[0.1.0]: https://github.com/Digitmedia-de/ha-syncvita/commits/35f2538
