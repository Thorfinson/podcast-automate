# Gezielte Recherche und Dossierkorrekturen

Diese Prüfung deckt die Änderungen an Abschnittssuche, gezielten Dossieränderungen und Wiederaufnahme ab. Sie ersetzt keine fachliche Abnahme eines fertigen Podcasts und keinen Laufzeitvergleich mit echten Modellen.

## Automatisierte Prüfungen

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_research tests.test_research_quality tests.test_research_refinement tests.test_studio_progress tests.test_scripting -q
```

Geprüft werden insbesondere:

- Eine fehlende Definition wird unter 187 gespeicherten Abschnitten gefunden, einschließlich Seitenbezug und benachbartem Kontext. Deutsche Beugungen technischer Begriffe können zu englischen Originalbegriffen passen.
- Lokales Nachlesen benötigt keinen Download und kann auch nach Erreichen des Quellen- oder Web-Suchlimits eine Frage beantworten. Modellaufrufe bleiben budgetiert.
- Unbeteiligte Befunde bleiben unverändert; unbekannte IDs, doppelte Änderungen und nicht erlaubte Eingriffe werden abgewiesen.
- Gezielt reparierte Belege werden erneut validiert. Ein Treffer oder formal gültiger Beleg allein genügt nicht zum Bestehen der Quellen- und Vollständigkeitsprüfung.
- Gespeicherte Änderungen werden nach einer Unterbrechung wiederverwendet; veränderte Eingaben oder beschädigte Zwischenstände verhindern falsche Wiederverwendung.
- Abgeschlossene alte Prüfrunden bleiben wiederaufnehmbar. Ein alter Lauf, der nach Suche und Download vor dem nächsten Dossier angehalten wurde, übernimmt die Quellen und verwendet anschließend gezielte Änderungen.
- Aktuelle Quellen-Einwände ersetzen überholte Einwände. Veraltete Gesamtbewertungen erzeugen währenddessen keine zusätzliche Aufgabenliste; die ursprünglichen Leitfragen werden vor Freigabe vollständig neu geprüft.

Stand 16.09.2026: **86 Tests bestanden**, Modelle und Downloads simuliert. Ausgeführt mit Python 3.12 in der Sandbox, Pydantic 2.13.5 und der Projektversion von pypdf 6.18.1. Die normale Projektlaufzeit Python 3.13 war in der Sandbox nicht ausführbar; die automatische Freigabe eines erhöhten Teststarts scheiterte an der Auslastung des Prüfmodells.

Ein zusätzlicher Gesamtlauf von 316 Tests war **nicht vollständig grün**: Er meldete zwei Fehlschläge und acht Fehlerausgaben, unter anderem fehlende Paketpfade in Kindprozessen, Timeouts beim Beenden von Testprozessen und abgebrochene lokale HTTP-Verbindungen. Einige Fehlerausgaben betreffen Aufräumarbeiten desselben Tests. Diese Ergebnisse aus der Ersatzlaufzeit belegen keine fehlerfreie Gesamtsuite; die unveränderten Prozess-/HTTP-Bereiche wurden in dieser Änderung nicht repariert.

## Kontrollprüfung an gespeicherten Recherchequellen

Zusätzlich wurde die neue Suche ohne Modell- oder Netzwerkaufruf gegen die vorhandenen Dateien des Recherchelaufs `run_20260916_121103_388868_a082fa2a`, Runde 002, ausgeführt. Eingaben waren der gespeicherte Quellenindex, der damalige Modellausschnitt und die tatsächliche offene Frage nach singulären und synergischen Satisfier-Typen sowie Verhaltensstudien.

Ergebnis: Die zuvor nicht vorgelegte Definition `src_6622dc67b5fea004#sec_f1dc13c2699a5406` wurde gefunden. Die Auswahl ergänzte sechs Abschnitte mit insgesamt 4.460 Textzeichen. Der bestehende Ausschnitt dieser Quelle hatte vier von 187 Abschnitten enthalten. Der Forschungsauftrag und seine Dateien wurden dabei nicht verändert.

Damit ist die Auffindbarkeit der Definition nachgewiesen. Ob zusätzliche Verhaltensstudien die weitergehenden Aussagen tragen, bleibt eine gesonderte fachliche Prüfung. Aus dieser Kontrolle lässt sich noch keine prozentuale Beschleunigung eines vollständigen Recherchelaufs ableiten.
