# Implementierungsplan: Deep-Dive-Serien

## Verbindlicher Fokus

Die Produkt- und Architekturentscheidungen stehen in [SPEC.md](../SPEC.md). Dieser Plan beschreibt die Umsetzung des am 2026-09-10 priorisierten Hauptfalls: Thema vorgeben, Quellen recherchieren und eine zusammenhängende Podcastserie erhalten, deren Folgenzahl und Gesamtdauer sich aus Themenumfang und gewünschter Tiefe ergeben. Jede Folge dauert höchstens 30 Minuten.

Die Serie muss ausführliche Erklärungen, Beispiele, Belege, Gegenpositionen und eine nachvollziehbare Reihenfolge bieten. Tutor, Quiz, Karteikarten, Prüfungsmodus und Wiederholungsplanung sind zurückgestellt. Sie sind keine Voraussetzung für den Recherchekern oder die Abnahme des MVP.

## Konsequenzen für die Architektur

| Entscheidung | Umsetzung |
| --- | --- |
| Thema als Einstieg | Themenauftrag und aktive Quellenrecherche ergänzen die Ingestion eigener Dateien. |
| Serie als Standard | Serienplan, Abdeckungsmatrix und Abhängigkeiten entstehen vor den einzelnen Skripten. |
| Inhalt bestimmt den Serienumfang | Folgenzahl und Gesamtdauer werden aus Themenabdeckung und Tiefe abgeleitet; weitere nötige Folgen sind möglich. |
| Gründliche Erklärungen | Wissensmodell enthält Voraussetzungen, Mechanismen und ausgearbeitete Beispiele zusätzlich zu Claims. |
| Zusammenhängende Folgen | Jeder Skriptaufruf kennt relevante Quellen, den Gesamtplan, bereits Erklärtes und offene Fragen. |
| Hörbares MVP | Audio-Export ist Bestandteil des fertigen MVP; pro Lauf bleibt die explizite Audio-Freigabe erhalten. |
| Ein Ausgabeformat | Ein Deep-Dive-Planer und -Generator; keine generische Routing-Schicht für spätere Modi. |

## Meilenstein 0: Eine Folge als Qualitätsmaßstab

Vor einer vollständigen automatischen Produktion wird ein fachlicher Pilot aus den Nutzerbeispielen gewählt. Der erste vorgeschlagene Pilot ist die Frage nach energiebasierten Modellen im Maschinenlernen, ausgehend von konkret recherchierten Arbeiten von Yann LeCun und Alfredo Canziani.

Dafür entstehen zunächst:

1. ein Themenauftrag mit Leitfrage, Vorwissen und gewünschter inhaltlicher Tiefe,
2. ein begrenztes, geprüftes Quelldossier mit Herkunft und Quellenabschnitten,
3. ein vorläufiger Serienplan mit inhaltlich begründeter Folgenzahl, Abhängigkeiten und Laufzeitschätzungen,
4. ein vollständig ausgearbeitetes Skript für eine inhaltlich zentrale Folge bis 30 Minuten,
5. eine redaktionelle Bewertung der Erklärungstiefe und der Quellenbindung.

Eine zentrale Erklärfolge ist besonders aussagekräftig: An ihr lässt sich prüfen, ob das System einen Zusammenhang wirklich entfaltet und ein Beispiel durchführt. Ein gelungener Einstieg allein weist diese Fähigkeit noch nicht nach.

Dieser Meilenstein liefert Referenzausgaben und Prüfkriterien. Er ersetzt nicht die spätere vollständige Pilotserie. Die Recherche des Pilotinhalts und die Erzeugung der Artefakte sind noch ausstehende Arbeiten; die Dokumentation enthält bisher keine fertigen fachlichen Ergebnisse.

## Phase 1: Projektstruktur und Datenverträge

- Projektkonfiguration aus `TopicBrief` und verbindliche Artefaktpfade aus der Spezifikation umsetzen.
- Schemas für Quellen, Wissensmodell, Serienplan, Episodenplan, Qualitätsbericht und Manifest implementieren.
- Stabile IDs und die maschinenlesbare Referenzsyntax im Skript festlegen.
- `pla init` und `pla ingest` für Markdown, Text und PDF bauen.
- Quellen abschnittsweise importieren, Hashes und Rechte erfassen, Duplikate und Importfehler behandeln.
- Run-Stände einfrieren und Stufen wiederaufnehmbar machen.

**Abnahme:** Ein lokales Fixture lässt sich importieren und validieren. Ungültige IDs und fehlende Quellenabschnitte werden erkannt. Es gibt keine erforderlichen Tutor-Felder.

## Phase 2: Themengeleitete Recherche

- `pla research` mit echten Such- und Abrufwerkzeugen implementieren.
- Aus Leitfrage und Vorwissen Teilfragen, Grundlagen und Suchbegriffe ableiten.
- Personen und Werke anhand konkreter Quellen zuordnen.
- Quellenkandidaten, Auswahlgründe, Zugriffsfehler und Recherchegrenzen dokumentieren.
- Primärquellen und fachliche Einordnungen einlesen; Suchausschnitte nicht als Volltext behandeln.
- Abdeckung prüfen und notwendige ergänzende Suchrunden begrenzen.

**Abnahme:** Ein Themenauftrag funktioniert ohne mitgelieferte Quelldateien. Nicht zugängliche oder unzureichende Quellen führen zu sichtbaren Lücken. Zuschreibungen an Personen bleiben überprüfbar.

## Phase 3: Wissensmodell und Synthese

- Begriffe, Claims, Evidence-Einträge, Gegenpositionen und Unsicherheiten extrahieren.
- Voraussetzungen und Erklärabhängigkeiten verknüpfen.
- Mechanismen in nachvollziehbare Schritte zerlegen und Beispiele mit Quellen verankern.
- Perspektiven vergleichen und redaktionelle Schlussfolgerungen markieren.
- Recherche-Briefing, Argumentkarte und offene Fragen aus demselben Modell ableiten.

**Abnahme:** Die priorisierten Fragen haben eine Quellenbasis oder klar ausgewiesene Lücken. Das Modell trägt eine Erklärung über mehrere Folgen. Widersprüche werden eingeordnet, ohne Scheinkonsens zu erzeugen.

## Phase 4: Serien- und Episodenplanung

- `pla plan` mit inhaltlich abgeleiteter Folgenzahl und harter Obergrenze von 30 Minuten je Folge bauen.
- Folgen nach Voraussetzungen und aufeinander aufbauenden Fragen anordnen.
- Bei zusätzlichem Erklärbedarf weitere Folgen einplanen; keine feste Gesamtlänge oder maximale Folgenzahl einbauen.
- Claims, Begriffe und Teilfragen in einer Abdeckungsmatrix zuweisen.
- Erklärschritte, Beispiele und Gegenpositionen in Szenen mit Zeitbudget planen.
- Vertagte Kernfragen einer späteren Folge zuordnen oder begründet ausschließen.
- Wortzahl- und Pausenschätzung für Laufzeiten einführen; nach dem Audio-Pilot kalibrieren.

**Abnahme:** Der Plan deckt die priorisierten Fragen in der gewünschten Tiefe ab, begründet seine Folgenzahl und hat keine verlorenen Kerninhalte oder unerklärten Voraussetzungen. Die Gesamtdauer ergibt sich aus den Folgen. Ein ausdrücklich genannter Zeitwunsch und nötige Abweichungen werden sichtbar gemacht.

## Phase 5: Skripterstellung und Qualitätsprüfungen

- `pla script` für die gesamte Serie und einzelne Folgen implementieren.
- Pro Folge relevante Quellenabschnitte und Serienkontext zusammenstellen.
- Vollständige, sprechbare Erklärungen mit zwei funktionalen Host-Rollen erzeugen.
- Wissensmodell-Referenzen maschinenlesbar im Skript halten.
- `pla check` mit deterministischen Prüfungen und getrennt ausgewiesenen inhaltlichen Reviews umsetzen.
- Quellenpassung, Erklärungstiefe, Szenenfortschritt und Wiederholungen über Folgengrenzen hinweg prüfen.
- Ergänzende Recherche und Überarbeitung auf konkret festgestellte Lücken begrenzen.
- Qualitätsberichte an Artefakthashes binden; Änderungen machen betroffene Prüfungen ungültig.

**Abnahme:** Eine vollständige Serie aus Skripten beantwortet die Leitfrage schrittweise. Eine oberflächliche Zusammenfassung besteht den Tiefencheck auch dann nicht, wenn alle Referenzen formal gültig sind.

## Phase 6: Audio, Export und vollständiger Pilot

- TTS-Anbindung, Sprecherstimmen und Aussprache zentraler Begriffe konfigurieren.
- Segmentweise rendern, cachen, zusammensetzen und Lautheit normalisieren.
- Kostenschätzung vor Audio-Freigabe bereitstellen.
- `pla render` nur mit aktueller Qualitätsprüfung und expliziter Freigabe zulassen.
- Tatsächliche Laufzeiten messen und zu lange Folgen vor dem finalen Export korrigieren.
- Kapitel aus der Audio-Zeitleiste sowie Transkripte und Show Notes exportieren.
- Unterbrochene Folgen ohne komplette Neuberechnung fortsetzen.
- Die vollständige Pilotserie redaktionell anhören und anhand der Qualitätskriterien bewerten.

**Abnahme:** Der Hauptfall ist als zusammenhängende Serie mit inhaltlich begründetem Umfang hörbar. Zusätzliche nötige Folgen können produziert werden. Keine Audiofolge überschreitet 30 Minuten. Geplante und tatsächliche Gesamtdauer werden ausgewiesen, und alle Begleitdateien sind vorhanden.

## Evaluation und Fixtures

Die verbindlichen Fixtures stehen in der Spezifikation:

- `fixtures/simple_topic` für schnelle technische Prüfungen,
- `fixtures/mechanism_series` für Tiefe, Abhängigkeiten und langen Serienumfang,
- `fixtures/conflicting_perspectives` für Quellenkritik und die Trennung von Positionen und Evidenz.

Referenzausgaben benennen erwartete Claims, Erklärschritte, Beispiele, Grenzen und problematische Fälle. Das Serienfixture prüft zusätzlich Themenabdeckung, Reihenfolge, Wiederholungen und die Erweiterung um weitere inhaltlich nötige Folgen. Eine feste Gesamtlänge oder Folgenzahl ist kein Abnahmekriterium. Der frühere prüfungsbezogene Fixture-Fall wird nicht für den MVP benötigt.

Die Bewertungskriterien stehen in [system-quality-assessment.md](system-quality-assessment.md). Formale Validierung und redaktionelle Bewertung werden getrennt berichtet. Ein bestandener Modellreview allein belegt keine Hörqualität.

## Offene technische Entscheidungen

Vor der jeweiligen Implementierungsphase sind konkret zu entscheiden:

| Entscheidung | Spätestens erforderlich |
| --- | --- |
| Programmiersprache, CLI-Framework und Schema-Bibliothek | Phase 1 |
| Skriptformat und Referenzsyntax | Phase 1 |
| Such- und Abrufprovider, Umgang mit PDF- und Transkriptzugriff | Phase 2 |
| LLM-Provider, Modelle, Kontextaufteilung, Retry- und Kostenlimits | Phase 3 |
| Bewertungsschema für Evidenzpassung und Erklärungstiefe | Vor der ersten Skriptabnahme |
| TTS-Provider, Stimmen, Aussprachelexikon und Audio-Werkzeuge | Phase 6 |

Diese Entscheidungen sind noch offen. Die neue Priorisierung wählt keinen Anbieter und implementiert noch keine Pipeline.

## Spätere Optionen

Nach Abnahme des Deep-Dive-MVP können Tutor-Modus, Quiz, Karteikarten, Prüfungsmodus, Wiederholungsplanung, zusätzliche Stimmen, Sounddesign und weitere Oberflächen neu priorisiert werden. Das Quellen- und Wissensmodell kann dafür weiterverwendet werden; zusätzliche Modi dürfen den ersten Hauptfall nicht verzögern.
