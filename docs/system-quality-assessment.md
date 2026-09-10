# Qualitätsbewertung: Deep-Dive-Serien

## Stand und Bewertungsgrundlage

Der priorisierte Hauptfall ist eine quellengebundene Podcastserie von drei bis vier Stunden mit höchstens 30 Minuten pro Folge. Bewertet werden Recherche, Erklärungstiefe, Aufbau über mehrere Folgen und Hörqualität.

Das Repository enthält derzeit Anforderungen und einen Implementierungsplan. Es gibt noch keine implementierte Pipeline, vollständigen Pilotoutputs oder gemessenen Qualitätsresultate. Frühere Zahlenbewertungen des Definitionsstands sind keine nachgewiesenen Produkteigenschaften und werden durch konkrete Prüfkriterien ersetzt.

Tutor-Pädagogik, Quiz, Lernstandsdiagnostik und Wiederholungsplanung sind außerhalb dieser MVP-Bewertung. Verständliche Erklärungen und sinnvoll aufgebaute Grundlagen bleiben entscheidend.

## Prüfkriterien

| Bereich | Woran gute Qualität erkennbar ist | Unzureichendes Ergebnis |
| --- | --- | --- |
| Themenabdeckung | Priorisierte Fragen haben begründete Antworten oder sichtbare Grenzen. | Ein langes Skript lässt zentrale Teile der Leitfrage aus. |
| Recherche | Konkrete Quellenabschnitte tragen die Aussagen; Herkunft und Gegenpositionen werden eingeordnet. | Suchausschnitte, Quellenlisten oder bekannte Namen ersetzen die Prüfung der Aussagen. |
| Erklärungstiefe | Begriffe, Voraussetzungen und Erklärschritte führen zu einem nachvollziehbaren Zusammenhang. | Mehrere Definitionen werden genannt, ohne das Wie und Warum zu erklären. |
| Beispiele | Ein konkretes Beispiel wird Schritt für Schritt durchgearbeitet und mit der Erklärung verbunden. | Beispiele bleiben kurze Stichworte oder schmückende Anekdoten. |
| Evidenz und Grenzen | Befunde, Interpretation, Hypothesen und Unsicherheit werden unterschieden. | Eine einzelne Perspektive wird als gesicherter Gesamtstand ausgegeben. |
| Serienaufbau | Folgen beantworten unterschiedliche Fragen und bauen auf bereits eingeführten Grundlagen auf. | Jede Folge beginnt erneut mit demselben Überblick. |
| Zusammenhang | Vertagte Fragen werden später aufgenommen; die letzte Folge verbindet die Ergebnisse. | Folgen stehen nebeneinander oder verlieren zentrale offene Fragen. |
| Dialog | Host-Nachfragen bewirken Präzisierung, Herleitung, Kritik oder ein vertieftes Beispiel. | Sprecher wechseln nur zwischen kurzen Behauptungen und Zustimmung. |
| Hörbarkeit | Tempo, Aussprache, Pausen und Kapitel unterstützen die Erklärung. | Ein formal korrektes Skript ist gesprochen schwer nachvollziehbar. |
| Laufzeit | Budget, Schätzung und gemessene Dauer werden ausgewiesen; jede Audiofolge bleibt bei höchstens 30 Minuten. | Wortzahl wird mit Tiefe gleichgesetzt oder Überlänge erst beim Hören bemerkt. |

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

Diese Piloten sind Rechercheaufträge, keine bereits geprüften fachlichen Ergebnisse. Der Implementierungsplan priorisiert zunächst eine zentrale Erklärfolge des ersten Themas als Qualitätsmaßstab.

## Vorgehen zur Abnahme

1. Ein kleines Quelldossier und erwartete Kerninhalte redaktionell festlegen.
2. Einen Gesamtplan und eine zentrale Folge prüfen, bevor die vollständige Serie produziert wird.
3. Erkannte Lücken in Wissensmodell, Plan und Skript korrigieren.
4. Die vollständige Skriptserie gegen Quellen, Abdeckung und Zusammenhang prüfen.
5. Nach Freigabe Audio erzeugen; Aussprache, tatsächliche Laufzeiten und Hörverständlichkeit prüfen.
6. Die vollständige Pilotserie bewerten und verbleibende Mängel konkret dokumentieren.

Automatische Prüfungen, Modellbewertungen und menschliche Befunde bleiben im Qualitätsbericht getrennt. Blockierende Befunde müssen vor finalem Export behoben sein. Die genauen technischen Gates und die Definition of Done stehen in [SPEC.md](../SPEC.md).
