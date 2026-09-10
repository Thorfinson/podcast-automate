# Podcast Automate: Deep-Dive-Serien

Ein persönliches Recherche-zu-Podcast-System: Ein Thema vorgeben und daraus eine zusammenhängende, quellengebundene Deep-Dive-Serie entwickeln. Themenumfang und gewünschte Tiefe bestimmen, wie viele Folgen nötig sind. Die Gesamtdauer und Folgenzahl haben keine feste Vorgabe; einzelne Folgen dauern höchstens 30 Minuten.

## Ausführbarer Stand: Version 0.1

Die erste Implementierung enthält Projektverwaltung, eine Codex-Abo-Verbindungsprobe und einen Qwen-Worker mit automatischer MP3-Montage. [Installation und erste Proben unter Windows 11](docs/windows-quickstart.md).

| Befehl | Bereits implementiert |
| --- | --- |
| `pla init` | Validierten Themenauftrag und lokale Projektstruktur anlegen |
| `pla doctor` | Installation, Abo-Anmeldung und TTS-Umgebung prüfen |
| `pla text-probe` | Strukturierte Codex-Antwort über die bestehende Abo-Anmeldung anfordern |
| `pla audio-probe --approve-audio` | Mitgelieferten deutschen Dialog lokal sprechen und automatisch montieren |
| `pla status` | Fortschritt, Fehler und veränderte Ergebnisse anzeigen |
| `pla resume` | Unterbrochene Proben mit gültigen Ergebnissen fortsetzen |
| `pla schemas` | Die implementierten Datenverträge als JSON-Schemas exportieren |

Der lokale Audioweg erzeugt MP3, Kapitel, Transkript und Messberichte. Die Montage ist mit echten FFmpeg-Aufrufen getestet; die Modellantworten werden in den Tests simuliert. Echte Codex-Abo-Aufrufe und Qwen auf der Radeon sind noch auf dem Zielrechner zu prüfen. Recherche und vollständige Serienproduktion sind die nächsten Ausbaustufen.

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

1. Themenauftrag, Vorwissen und gewünschte inhaltliche Tiefe festhalten.
2. Quellen recherchieren, importieren und bewerten.
3. Ein gemeinsames Wissensmodell mit Aussagen, Belegen, Voraussetzungen und Unsicherheiten aufbauen.
4. Einen Serienplan mit Themenabdeckung, daraus abgeleiteter Folgenzahl und geschätzter Laufzeit erstellen.
5. Folgen einzeln als ausführliche, sprechbare Skripte mit zwei Hosts ausarbeiten.
6. Quellenbindung, Tiefe, Zusammenhang und Laufzeit prüfen.
7. Nach Audio-Freigabe lokal sprechen, automatisch montieren und MP3-Folgen mit Kapiteln, Transkripten und Show Notes exportieren.

Audio-Export gehört zum MVP. Ein Lauf kann zur Prüfung bei den Skripten enden; Audio wird ausschließlich mit expliziter Freigabe und bestandenen blockierenden Qualitätsprüfungen erzeugt.

Tutor-Modus, Quiz, Karteikarten, Prüfungsmodus und Wiederholungsplanung sind spätere Optionen und keine MVP-Anforderungen.

## Geplanter Betrieb

Das Zielsystem ist Windows 11 mit einer AMD Radeon RX 9070 XT. Recherche und Skripte nutzen zunächst Codex CLI mit dem vorhandenen ChatGPT-Abo; Claude Code ist eine spätere Alternative. TTS soll lokal laufen. Qwen3-TTS ist der erste Kandidat für den Machbarkeitstest, noch keine auf diesem Rechner bestätigte Lösung. Die technischen Grundlagen und Quellen stehen im [Implementierungsplan](docs/personal-learning-podcast-system-plan.md).

Sprecherwechsel, Pausen, Montage, Lautheitsanpassung, Kapitel und Export werden automatisiert. Manueller Audioschnitt gehört nicht zum Bedienablauf. Zusätzliche bezahlte APIs sind keine Voraussetzung des MVP. Bei ausgeschöpftem Abo-Kontingent pausiert der Lauf und bewahrt bereits fertige Ergebnisse.

## Geplante vollständige Pipeline

Die folgenden Befehle beschreiben das Ziel der weiteren Implementierung. Der vollständige `pla run` ist noch nicht verfügbar:

```powershell
pla doctor
pla init .\my-topic --topic "Energiebasierte Modelle verstehen"
pla run .\my-topic --approve-audio
pla status .\my-topic
```

`pla resume .\my-topic` setzt einen unterbrochenen Lauf fort. Ohne `--approve-audio` endet `pla run` vor der Audioerzeugung. Einzelne Arbeitsschritte lassen sich ebenfalls ausführen:

```bash
pla init ./my-topic --topic "Energiebasierte Modelle verstehen"
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

Das Grundgerüst und die technischen Proben sind implementiert. Der Code verwendet Python 3.12, argparse aus der Standardbibliothek, Pydantic und YAML. Die Tests prüfen unter anderem Abo-Pausen, ungültige Antworten, Wiederaufnahme und Audio-Montage. Es gibt noch keine recherchierte Pilotserie oder bestätigte Hörqualität auf dem Zielrechner.

## Dokumentation

- [docs/windows-quickstart.md](docs/windows-quickstart.md): ausführbare Befehle, Windows-Installation und aktueller Funktionsumfang.
- [SPEC.md](SPEC.md): verbindlicher Hauptfall, Architektur, Datenverträge und Abnahmekriterien.
- [docs/personal-learning-podcast-system-plan.md](docs/personal-learning-podcast-system-plan.md): technische Startentscheidungen, sechs Meilensteine und konkrete Abnahmen.
- [docs/system-quality-assessment.md](docs/system-quality-assessment.md): Bewertungskriterien für Recherche, Tiefe, Serienaufbau und Hörqualität.

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Siehe [LICENSE](LICENSE).
