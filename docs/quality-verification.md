# Verifikation der Lehrprüfungen am 13.09.2026

Diese Verifikation bezieht sich auf `research.v2-foundations`, `script.v3-teaching`, `teaching_design.v2` und `teaching.v3`. Die anschließende Erweiterung `script.v4-openrouter-dialogue` ergänzt die Anbieterwahl pro Skriptlauf und präzisiert die Vorgaben zu gesprochener Sprache und frei gewählten Sprecherwechseln. Neue Skriptläufe müssen vor dem Schreiben eine quellengeprüfte Lehrplanung und danach getrennte Quellen-, Lese-, redaktionelle und Lehrprüfungen durchlaufen. Die [Arbeitsweise](teaching-design.md) beschreibt die Abbruchbedingungen und gespeicherten Ergebnisse.

`script.v5-dialogue-polish` ergänzt anschließend den eigenständigen Polishing-Durchgang und einen Vergleich von Bedeutung, Vollständigkeit, Rollen und gesprochener Sprache. Für diesen Stand bestanden **101 Tests in 17,578 Sekunden, keine übersprungen**. Sechs neue Tests prüfen unter anderem die Übergabe der überarbeiteten Fassung an die abschließenden Reviews, die Wiederaufnahme zwischen Überarbeitung und Vergleich, falsche Belege, bleibende inhaltliche Veränderungen und die Audio-Sperre bei beschädigter oder entfernter Polishing-Stufe.

Zusätzlich wurde der ausgewählte Logarithmus-Ausschnitt mit zwei echten Codex-Aufrufen überarbeitet und verglichen. Der Vergleich nahm die neue Fassung an; ein weiterer Vergleich wies eine absichtlich eingeführte falsche Aussage zurück. Die [archivierten Eingaben und Urteile](../evals/dialogue_polishing/README.md) dokumentieren diese drei Aufrufe. Sie ersetzen weder Quellenprüfung noch einen Test mit tatsächlichen Hörern.

## Automatisierte Prüfungen

Auf Windows bestanden **82 Tests in 14,955 Sekunden, keine übersprungen**:

```powershell
$ffmpegBin = (Resolve-Path .\tools\ffmpeg\bin).Path
$env:PATH = "$ffmpegBin;$env:PATH"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Die Tests prüfen unter anderem, dass fehlende notwendige Recherche das Schreiben blockiert, Begriffsabhängigkeiten nicht vorwärts zeigen, erfundene Zitate und ausgelassene Kriterien abgewiesen werden und Leserfragen keine Musterantworten enthalten. Ein redaktionelles Nein kann andere positive Urteile überstimmen. Gemeldete Erklärungslücken dürfen nicht stillschweigend verschwinden; eine fachlich nicht erforderliche Vertiefung braucht eine ausdrückliche Begründung. Wiederaufnahme erhält fertige Leseprüfungen und begrenzt Reparaturversuche. Veränderte Eingaben oder Prüfversionen erlauben keine Wiederverwendung alter Freigaben. Beschädigte Lehrartefakte blockieren auch ausdrücklich freigegebenes Audio.

Diese Tests verwenden kontrollierte Modellantworten, um das Programmverhalten zu prüfen. Sie messen keine Unterrichtsqualität. FFmpeg-Montage und Kapitelprüfung laufen zusätzlich mit echten Audiodateien.

Nach der OpenRouter-Erweiterung bestanden **95 Tests in 15,118 Sekunden, keine übersprungen**. Die 13 zusätzlichen Tests prüfen Authentifizierungsheader, strukturierte Antworten, Abbruch bei ungültigen oder abgeschnittenen Ausgaben, sichere Fehlertexte, Key-Schutz, Anbieterbindung und Wiederaufnahme mit einem anderen Key. Auch der vollständige Skriptweg mit allen Qualitätsstufen und ausstehender Audiofreigabe wird über den OpenRouter-Adapter geprüft. Die API-Antworten sind hierbei simuliert; ein echter OpenRouter-Aufruf sowie ein Geschwindigkeits- oder Qualitätsvergleich der dort angebotenen Modelle stehen aus. Die abschließende Ergänzung zur Abweisung fehlerhafter Antwortobjekte und von `:online`-Modellvarianten bestand erneut alle 13 Adaptertests.

## Echte Modellprüfungen

Die [wiederholbare Auswertung](../evals/teaching_quality/README.md) ruft dieselbe Funktion wie die Produktion auf. Jeder Fall erhielt drei getrennte Modellaufrufe über die konfigurierte Codex-Abo-Verbindung: einen Leser, eine redaktionelle Prüfung ohne Lehrplan und einen Prüfer mit Lernzielen. Die erwarteten Urteile wurden keinem dieser Aufrufe übergeben.

| Fester Prüffall | Erwartung | Ergebnis mit `teaching.v3` |
| --- | --- | --- |
| Tatsächlicher, vom Nutzer abgelehnter Pilot | Ablehnen | Abgelehnt: Einstieg, Aufbau, Dialog und Tiefe |
| Kurzer Text mit Jahreszeiten-Begriffen ohne Herleitung | Ablehnen | Abgelehnt: alle sieben Lehrkriterien |
| Ausgearbeitete Einführung in die Jahreszeiten | Annehmen | Angenommen: sieben Kriterien und drei Lernziele |

Die [maschinenlesbare Ergebnisakte](../evals/teaching_quality/results/2026-09-13-v3.json) archiviert die tatsächlichen Belege, Leserantworten, Einzelurteile und Prüfsummen. Rohantworten und Aufrufmetadaten liegen zusätzlich lokal unter `projects/quality-evaluation-v3/`. Die Laufzeitkonfiguration wählte keinen Modellnamen ausdrücklich; diese Prüfung behauptet daher keine festgeschriebene Modellversion.

Der Pilot scheiterte insbesondere daran, dass die technische Rechnung vor einer ausreichenden Einordnung beginnt, sich beim Beispielwechsel die Bedeutung der betrachteten Möglichkeiten verschiebt und aufeinanderfolgende Sprecherbeiträge einen Einwand nicht gemeinsam ausarbeiten. Das sind überprüfbare Probleme im gesprochenen Text. Eine vollständige Gliederung hätte sie nicht behoben.

## Was während der Entwicklung nicht funktionierte

Frühere Prüfungen wurden nicht als Erfolg gewertet: In `teaching.v1` verlangte der simulierte Leser teilweise zusätzliche Details außerhalb der eigentlichen Lernfrage. Deshalb muss der Prüfer nun jede gemeldete Lücke begründet einordnen. In `teaching.v2` wurde der abgelehnte Pilot trotzdem angenommen. Deshalb wurde die zusätzliche redaktionelle Prüfung eingeführt, die weder den Lehrplan noch die Leserantworten oder andere Urteile kennt.

Auch der zunächst als positiv gedachte Jahreszeiten-Text bestand nicht: Der Zusammenhang zwischen Achsenneigung, Sonnenhöhe und Tageslänge war nicht ausreichend entwickelt. Der Kontrolltext wurde fachlich ergänzt und mit neuer Prüfsumme erneut bewertet. Die früheren Ergebnisse bleiben lokal in `projects/quality-evaluation/`, `projects/quality-evaluation-v2/` und `projects/quality-positive-validation/` erhalten. Ein früher echter Lehrplanlauf deckte außerdem Lücken und Begriffswechsel im Pilotplan auf; die spätere Unterscheidung notwendiger Recherche von erklärbaren Forschungsgrenzen ist durch automatisierte Tests geprüft.

## Reichweite der Ergebnisse

Die drei Fälle sind eine kleine, während der Entwicklung verwendete Kalibrierung. Sie sind kein unabhängiger Test über viele unbekannte Themen und keine Messung der Zuverlässigkeit bei wiederholten Modellaufrufen. Modellurteile können schwanken; mehrere getrennte Aufrufe sind keine unabhängigen menschlichen Fachgutachten. Die Lehrprüfung ersetzt weder Quellenprüfung noch tatsächliches Hören und Lernen. Ein vollständiger neuer Forschungs- und Serienlauf mit der endgültigen Prüfversion wurde hier nicht erzeugt.

Der nächste reale Auftrag muss dieselben Prüfungen bestehen und wird zuerst als lesbarer Text vorgelegt. Zur Abnahme gehören ein nachvollziehbarer Einstieg, der Weg von Voraussetzungen zum Mechanismus, ein durchgearbeitetes Beispiel, eine daraus entwickelte neue Einsicht und ein verständliches Gespräch. Die tatsächliche Nutzerbewertung bleibt maßgeblich; jeder weitere verworfene oder gelungene Text kann den festen Prüfsatz erweitern. Alle Modellberichte führen weiterhin `human_learning_validated: false`, und neue Audioerzeugung erfordert die bestehende ausdrückliche Textfreigabe.
