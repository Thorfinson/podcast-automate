# Qualitätsbewertung: Deep-Dive-Serien

## Stand und Bewertungsgrundlage

Der priorisierte Hauptfall ist eine quellengebundene Podcastserie, deren Umfang sich aus dem Thema und der gewünschten Tiefe ergibt. Gesamtdauer und Folgenzahl sind nicht fest vorgegeben; jede Folge dauert höchstens 30 Minuten. Bewertet werden Recherche, Erklärungstiefe, Aufbau über mehrere Folgen und Hörqualität.

Version 0.1 enthält eine CLI mit echter Themenrecherche, Quellenabruf, belegtem Dossier, Serienplanung, quellengeprüften Dialogskripten, Codex-Probe, Qwen-Worker, Wiederaufnahme und automatischer Audio-Montage für Proben. Die Tests prüfen simulierte Modellantworten und echte FFmpeg-Verarbeitung von Testsignalen. Zusätzlich wurden am 13.09.2026 lokale Qwen-Hörproben auf der RX 9070 XT sowie echte Recherche- und Skriptläufe abgeschlossen; [Nachweise](windows-pilot.md) sind dokumentiert. Der Nutzer hat die bisherigen Skripte trotz bestandener Modellreviews als zu oberflächlich zurückgewiesen. Quellen- und Strukturprüfungen belegen deshalb keine ausreichende Erklärungstiefe. Die überarbeitete redaktionelle Prüfung muss den gewünschten Anspruch und die tatsächliche Argumentation berücksichtigen. Vertonung und Hörabnahme der recherchierten Folge stehen noch aus.

Tutor-Pädagogik, Quiz, Lernstandsdiagnostik und Wiederholungsplanung sind außerhalb dieser MVP-Bewertung. Verständliche Erklärungen und sinnvoll aufgebaute Grundlagen bleiben entscheidend.

## Prüfkriterien

| Bereich | Woran gute Qualität erkennbar ist | Unzureichendes Ergebnis |
| --- | --- | --- |
| Themenabdeckung | Priorisierte Fragen haben begründete Antworten oder sichtbare Grenzen. | Ein langes Skript lässt zentrale Teile der Leitfrage aus. |
| Recherche | Konkrete Quellenabschnitte tragen die Aussagen; Herkunft und Gegenpositionen werden eingeordnet. | Suchausschnitte, Quellenlisten oder bekannte Namen ersetzen die Prüfung der Aussagen. |
| Erklärungstiefe | Begriffe, Voraussetzungen und Erklärschritte führen zu einem nachvollziehbaren Zusammenhang. | Mehrere Definitionen werden genannt, ohne das Wie und Warum zu erklären. |
| Verständlichkeit ohne Vorwissen | Vertraute Bilder und kleine Erklärschritte machen die Idee verständlich; notwendige Begriffe folgen danach. | Fach- oder Mathematiksprache setzt nicht erklärte Kenntnisse voraus. |
| Mentale Bilder | Wenige zusammenhängende Metaphern erklären einen Mechanismus und nennen die Grenze des Vergleichs. | Ein Bild ersetzt die Erklärung, führt in die Irre oder wird als tatsächlicher Forschungsbefund dargestellt. |
| Beispiele | Ein konkretes Beispiel wird Schritt für Schritt durchgearbeitet und mit der Erklärung verbunden. | Beispiele bleiben kurze Stichworte oder schmückende Anekdoten. |
| Evidenz und Grenzen | Befunde, Interpretation, Hypothesen und Unsicherheit werden unterschieden. | Eine einzelne Perspektive wird als gesicherter Gesamtstand ausgegeben. |
| Serienaufbau | Folgen beantworten unterschiedliche Fragen und bauen auf bereits eingeführten Grundlagen auf. | Jede Folge beginnt erneut mit demselben Überblick. |
| Serienumfang | Die Folgenzahl deckt den nötigen Erklärbedarf ab und wächst bei zusätzlichen inhaltlichen Anforderungen. | Kerninhalte werden wegen einer pauschalen Gesamtzeit- oder Folgenbegrenzung gekürzt. |
| Zusammenhang | Vertagte Fragen werden später aufgenommen; die letzte Folge verbindet die Ergebnisse. | Folgen stehen nebeneinander oder verlieren zentrale offene Fragen. |
| Dialog | Host-Nachfragen bewirken Präzisierung, Herleitung, Kritik oder ein vertieftes Beispiel. | Sprecher wechseln nur zwischen kurzen Behauptungen und Zustimmung. |
| Hörbarkeit | Tempo, Aussprache, Pausen und Kapitel unterstützen die Erklärung. | Ein formal korrektes Skript ist gesprochen schwer nachvollziehbar. |
| Automatische Produktion | Zwei beständige Stimmen, passende Übergänge, Lautheit und fertige Dateien entstehen ohne manuellen Schnitt. | Der Nutzer muss Audioschnipsel sortieren, verbinden oder im Editor reparieren. |
| Zuverlässigkeit | Abo-Pausen und technische Fehler lassen sich mit erhaltenen Ergebnissen fortsetzen. | Eine Unterbrechung erzwingt die Neuberechnung der Serie oder einen ungefragten API-Wechsel. |
| Laufzeit | Aus dem Inhalt geplante und tatsächlich gemessene Dauer werden ausgewiesen; jede Audiofolge bleibt bei höchstens 30 Minuten. | Eine feste Gesamtstundenzahl gilt als Qualitätsnachweis oder Wortzahl wird mit Tiefe gleichgesetzt. |

## Was der Tiefencheck leisten muss

Für jede zentrale Erklärfrage prüft die Redaktion zusammenhängend:

1. Sind die benötigten Begriffe klar, bevor sie entscheidend verwendet werden?
2. Wird erklärt, wie und warum ein Zusammenhang zustande kommt, soweit die Quellen das erlauben?
3. Trägt ein ausgearbeitetes Beispiel zum Verständnis bei?
4. Werden Voraussetzungen, Grenzen und relevante andere Deutungen behandelt?
5. Ist die Antwort auf die Folgenfrage am Ende wesentlich gehaltvoller als am Anfang?

Das reine Vorhandensein der fünf Elemente genügt nicht. Die Bewertung nennt konkrete gelungene oder fehlende Skriptpassagen und verweist auf zugehörige Quellen oder Wissensmodell-Einträge. Eine offene fachliche Frage darf offenbleiben, wenn die Grenzen der verfügbaren Erklärung nachvollziehbar sind.

Tiefe ist kein einfacher Zählwert. Quellen-IDs und Wortzahlen unterstützen die Prüfung, ersetzen aber keine Bewertung von Gedankengang und Evidenzpassung.

## Prüfung über Folgengrenzen hinweg

Vor der Abnahme einer Serie werden zusätzlich geprüft:

- Abdeckung: Jede priorisierte Teilfrage und jeder zentrale Claim hat einen begründeten Platz.
- Voraussetzungen: Eine Folge setzt nur bereits erklärtes oder ausdrücklich angegebenes Vorwissen voraus.
- Fortschritt: Jede Folge erweitert das Verständnis und besteht nicht überwiegend aus Wiederholung.
- Rückschauen: Wiederholungen helfen beim Anschluss und verdrängen keine neuen Erklärungen.
- Offene Fragen: Vertagte Inhalte werden später behandelt oder begründet aus dem Umfang genommen.
- Synthese: Die letzte Folge beantwortet die übergreifende Leitfrage anhand der zuvor aufgebauten Ergebnisse.

Ein gutes Einzelskript reicht als Nachweis für eine gute Serie nicht aus.

## Fachliche Piloten

Die Nutzerbeispiele liefern zwei mögliche Piloten:

- **Energiebasierte Modelle im Maschinenlernen:** prüfen, ob Begriffe, Voraussetzungen, Erklärschritte und konkrete Beispiele eine längere Serie tragen. Aussagen zu Yann LeCun und Alfredo Canziani müssen anhand konkreter Arbeiten zugeordnet werden.
- **Blutwerte:** prüfen, ob Grundlagen und unterschiedliche Arten fachlicher Aussagen nachvollziehbar getrennt werden und personenzentrierte Ausgangsquellen in eine breitere Quellenlage eingeordnet werden. Eine ungeklärte Personenangabe wird nicht stillschweigend einer Person zugeordnet.

Diese Piloten sind Rechercheaufträge, keine bereits geprüften fachlichen Ergebnisse. Nach einem frühen technischen Audiotest priorisiert der Implementierungsplan eine zentrale Erklärfolge des ersten Themas als fachlichen Qualitätsmaßstab.

## Früher Audiotest und automatische Prüfungen

Vor dem Aufbau der vollständigen Recherchepipeline wurde auf Windows 11 mit der Radeon RX 9070 XT eine deutsche Qwen-Hörprobe automatisch erzeugt und montiert. Sie enthält beide Stimmen, längere Erklärpassagen, Sprecherwechsel, Fachbegriffe, Zahlen und Einheiten. Die technische Ausführung ist nachgewiesen; die redaktionelle Bewertung ihrer Eignung steht noch aus.

Der Test dokumentiert Modell- und Laufzeitversionen, Speicherbedarf, Erzeugungsdauer, Verständlichkeit, Stimmenkonstanz und Aussprache. Eine kurze Hörprobe dient der anfänglichen Auswahl von Modell und Stimmen. Sie ist kein wiederkehrender manueller Schnittschritt.

Im Produktionslauf werden fehlende oder beschädigte Segmente, leere Ausgabe, auffällige Stille, Pegelfehler und unplausible Dauer automatisch geprüft. Betroffene Segmente erhalten begrenzte Reparaturversuche. Verbleibende Befunde blockieren den betroffenen finalen Export mit einer konkreten Fehlermeldung. Kapitel und Laufzeit werden an den tatsächlich montierten Audiodaten geprüft.

Eine ergänzende lokale Rücktranskription wird anhand bekannter Auslassungen und Wiederholungen bewertet. Sie darf nicht allein als Nachweis für korrekte Aussprache gelten. Automatische Fehlererkennung kann keine fehlerfreie oder durchgehend natürliche Sprachausgabe garantieren; die Hörprüfung des Piloten bleibt ein eigenes Abnahmekriterium.

## Vorgehen zur Abnahme

1. Aus tatsächlicher Recherche ein belegtes Dossier erstellen und dessen Kerninhalte sowie Lücken redaktionell prüfen.
2. Einen Gesamtplan und eine zentrale Folge prüfen, bevor die vollständige Serie produziert wird.
3. Erkannte Lücken in Wissensmodell, Plan und Skript korrigieren.
4. Die vollständige Skriptserie gegen Quellen, Abdeckung und Zusammenhang prüfen.
5. Nach Freigabe Audio automatisch erzeugen und montieren; Aussprache, tatsächliche Laufzeiten und Hörverständlichkeit prüfen.
6. Die vollständige Pilotserie bewerten und verbleibende Mängel konkret dokumentieren.
7. Unterbrechung, Abo-Pause und defektes Segment gezielt erproben; fertige Ergebnisse müssen erhalten bleiben.

Diese redaktionelle Abnahme bewertet den MVP. Der normale Produktionslauf erfordert keine manuelle Bearbeitung jeder Folge. Automatische Prüfungen, Modellbewertungen und menschliche Befunde bleiben im Qualitätsbericht getrennt. Blockierende Befunde müssen vor finalem Export behoben sein. Die genauen technischen Gates und die Definition of Done stehen in [SPEC.md](../SPEC.md).
