# Podcast Automate: Deep-Dive-Serien

Ein persönliches Recherche-zu-Podcast-System: Ein Thema vorgeben und daraus eine zusammenhängende, quellengebundene Deep-Dive-Serie von drei bis vier Stunden entwickeln. Einzelne Folgen dauern höchstens 30 Minuten; typischerweise entstehen sechs bis acht Folgen.

## Hauptfall

Der Nutzer möchte ein anspruchsvolles Thema ausführlich erschließen. Der Einstieg kann eine Frage, eine These, eine Person oder eine konkrete Quelle sein. Eigene PDFs, Texte und Links können die Recherche ergänzen, sind aber keine Voraussetzung.

Beispiele für Themenaufträge:

- „Was bedeuten energiebasierte Modelle im Maschinenlernen? Erschließe das Thema anhand der Arbeiten von Yann LeCun und Alfredo Canziani, einschließlich Voraussetzungen, konkreter Beispiele, anderer Ansätze und offener Fragen.“
- „Ich möchte Blutwerte gründlich einordnen können. Recherchiere die Grundlagen und prüfe konkrete Aussagen aus von mir genannten Vorträgen oder Texten anhand weiterer fachlicher Quellen.“

Diese Beispiele beschreiben Rechercheaufträge. Zugeordnete Positionen und fachliche Aussagen müssen erst anhand konkreter Quellen geprüft werden.

## Was Tiefe hier bedeutet

- Begriffe und notwendige Grundlagen werden verständlich aufgebaut.
- Zentrale Zusammenhänge werden Schritt für Schritt erklärt und an Beispielen durchgearbeitet.
- Belege, Gegenpositionen, Grenzen und offene Fragen erhalten ausreichend Raum.
- Jede Folge beantwortet eine eigene Frage und baut auf dem bereits Erklärten auf.
- Die Serie führt am Ende die einzelnen Perspektiven zusammen.

Länge allein erfüllt den Qualitätsanspruch nicht. Wiederholte Überblickstexte, zusätzliche Floskeln oder gestreckte Dialoge gelten nicht als Vertiefung.

## MVP

Der erste MVP konzentriert sich vollständig auf Deep-Dive-Serien:

1. Themenauftrag, Vorwissen und Zeitbudget festhalten.
2. Quellen recherchieren, importieren und bewerten.
3. Ein gemeinsames Wissensmodell mit Aussagen, Belegen, Voraussetzungen und Unsicherheiten aufbauen.
4. Einen Serienplan mit Themenabdeckung und Zeitbudget erstellen.
5. Folgen einzeln als ausführliche, sprechbare Skripte mit zwei Hosts ausarbeiten.
6. Quellenbindung, Tiefe, Zusammenhang und Laufzeit prüfen.
7. Nach Audio-Freigabe MP3-Folgen mit Kapiteln, Transkripten und Show Notes exportieren.

Audio-Export gehört zum MVP. Ein Lauf kann zur Prüfung bei den Skripten enden; Audio wird ausschließlich mit expliziter Freigabe und bestandenen blockierenden Qualitätsprüfungen erzeugt.

Tutor-Modus, Quiz, Karteikarten, Prüfungsmodus und Wiederholungsplanung sind spätere Optionen und keine MVP-Anforderungen.

## Geplante CLI

```bash
pla init ./my-topic --topic "Energiebasierte Modelle verstehen" --total-minutes 210
pla research ./my-topic
pla ingest ./my-topic
pla model ./my-topic
pla plan ./my-topic
pla script ./my-topic
pla check ./my-topic
pla render ./my-topic --approve-audio
pla export ./my-topic
```

`pla script` und `pla render` können mit `--episode ep_001` auf eine einzelne Folge begrenzt werden. Das Thema und alle Folgedaten liegen in einem lokalen Projektordner. Die verbindlichen Artefaktpfade stehen in der Spezifikation.

## Entwicklungsstand

Das Repository enthält derzeit Spezifikation, Implementierungsplan und Evaluationskriterien. CLI, Recherche, Skriptgenerator und Audio-Rendering sind noch nicht implementiert.

## Dokumentation

- [SPEC.md](SPEC.md): verbindlicher Hauptfall, Architektur, Datenverträge und Abnahmekriterien.
- [docs/personal-learning-podcast-system-plan.md](docs/personal-learning-podcast-system-plan.md): Umsetzungsschritte und offene technische Entscheidungen.
- [docs/system-quality-assessment.md](docs/system-quality-assessment.md): Bewertungskriterien für Recherche, Tiefe, Serienaufbau und Hörqualität.

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Siehe [LICENSE](LICENSE).
