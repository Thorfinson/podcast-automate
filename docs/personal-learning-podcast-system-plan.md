# Implementierungsplan: Deep-Dive-Serien

Stand: 2026-09-13. Dieser Plan konkretisiert [SPEC.md](../SPEC.md). Version 0.1 enthält das Grundgerüst, technische Proben und einen echten Recherchepass bis zum belegten Dossier. [Windows-Anleitung und verfügbare Befehle](windows-quickstart.md).

Beim [Windows-Versuch am 13.09.2026](windows-pilot.md) bestanden echte Abo-Aufrufe, lokale Qwen-Hörproben und Wiederaufnahme. Nach dem Vergleich aller neun Stimmen wurden Aiden und Vivian gewählt. Der echte Such-/Quellenabruf wurde anschließend mit `pla research` implementiert und nachgewiesen: ein Thema führt zu heruntergeladenen Quellen, Abschnittsreferenzen, einem Dossier und einem Quellenreview. 62 Tests bestanden einschließlich FFmpeg. CLI, Quellenverträge, Dateiverwaltung, Prozessadapter, Status und Wiederaufnahme sind vorhanden. Ein kompaktes Wissensmodell, Serienentwurf und geprüfte Dialogskripte sind mit pla script umgesetzt. Vollständige Serienproduktion und Hörabnahme bleiben offen. Recherche wird somit vor dem vollständigen Quellen-zu-Audio-Pilot umgesetzt; ein vorbereitetes Testdossier ersetzt die reale Suche nicht.

## Ziel und feststehende Entscheidungen

Ein Thema führt zu einer recherchierten, zusammenhängenden Podcastserie. Begriffe, Mechanismen, Beispiele, Gegenpositionen und Unsicherheiten bekommen den nötigen Raum. Themenumfang und gewünschte Tiefe bestimmen die Folgenzahl. Es gibt keine feste Gesamtdauer und keine maximale Folgenzahl; einzelne Folgen bleiben gemäß Spezifikation bei höchstens 30 Minuten.

| Bereich | Entscheidung |
| --- | --- |
| Nutzung | Persönliches Projekt, lokale CLI und Projektdateien |
| Zielrechner | Windows 11 mit AMD Radeon RX 9070 XT |
| Textmodelle | Vorhandenes ChatGPT-/Codex-Abo zuerst; Claude Code als austauschbare Alternative |
| Sprachausgabe | Lokal; Qwen3-TTS ist der erste zu prüfende Kandidat |
| Produktion | Zwei beständige deutsche Host-Stimmen; Montage, Pausen, Lautheit, Kapitel und Export automatisch |
| Bedienung | Thema eingeben und Lauf starten; kein manueller Audioschnitt |
| Umfang | Inhaltlich begründete Serie, die bei zusätzlichem Erklärbedarf wachsen kann |
| Zurückgestellt | Tutor, Quiz, Karteikarten, Web-App, Veröffentlichung und aufwendiges Sounddesign |

Die Abos liefern die Textverarbeitung über die offiziellen CLI-Werkzeuge. Sie machen das Textmodell nicht lokal. Die Sprachausgabe soll auf dem eigenen Rechner laufen. Zusätzliche bezahlte Modell- oder Audio-APIs sind keine Voraussetzung des MVP.

## Technische Vorgaben für den Start

Diese Vorgaben sind umsetzbare Standardentscheidungen. Sie verlangen keine weitere Auswahl durch den Nutzer.

| Baustein | Umsetzung |
| --- | --- |
| Anwendung | Python 3.12, argparse aus der Standardbibliothek für die CLI, Pydantic 2 für validierte Datenverträge |
| Persistenz | YAML-/JSON-Artefakte, stabile IDs, atomare Dateischreibvorgänge und Run-Manifeste; zunächst keine Datenbank |
| Textadapter | Zunächst ein Adapter für Codex CLI mit Abo-Anmeldung und strukturierten Ergebnissen |
| Recherche | Suchwerkzeuge des gewählten CLI-Backends; tatsächlicher Abruf und lokale Speicherung zugänglicher Quellentexte |
| Import | Markdown, Text, HTML und textbasierte PDFs; unlesbare oder nicht zugängliche Inhalte als Lücke erfassen |
| Skript | Kanonisches script.yaml mit Regie und Referenzen; script.md als daraus erzeugte Lesefassung |
| Audio | Separater lokaler TTS-Prozess in eigener Python-Umgebung; Ein- und Ausgabe über Dateien |
| Montage | FFmpeg und ffprobe für Audioverarbeitung, Messung und MP3-Export |
| Tests | Kleine lokale Fixtures für Ablauf und Fehlerfälle; echte Modell- und GPU-Tests separat auf dem Zielrechner |

Die erste Implementierung verwendet argparse statt des zunächst vorgeschlagenen Typer und hält damit die CLI ohne weitere Framework-Abhängigkeit. Der Controller startet Unterprozesse mit Argumentlisten ohne Shell-Auswertung. Windows-Pfade und Leerzeichen werden berücksichtigt. Die TTS-Umgebung erhält eigene festgehaltene Paket- und Modellversionen, sobald die Kombination auf dem Zielrechner funktioniert.

Codex unterstützt Abo-Anmeldung sowie nichtinteraktive Aufrufe mit strukturierten Ausgaben. Darauf basiert der erste Adapter. Er nutzt die offizielle CLI und deren Anmeldung. Ein späterer Claude-Adapter verwendet denselben internen Auftrag-/Ergebnisvertrag; beide Adapter müssen nicht gleichzeitig gebaut werden. Quellen: [Codex-Anmeldung](https://learn.chatgpt.com/docs/auth), [Codex für Skripte](https://learn.chatgpt.com/docs/non-interactive-mode), [Claude Code für Skripte](https://code.claude.com/docs/en/headless).

Qwen3-TTS unterstützt Deutsch und bietet unterschiedliche Modellgrößen. Die 0.6B-CustomVoice-Variante wurde auf diesem Rechner mit AMD-PyTorch 2.9.1/ROCm 7.2.1 erfolgreich ausgeführt; [Versionen und Einrichtung](qwen-windows.md) sowie [Messwerte](windows-pilot.md) sind dokumentiert. Hörqualität, längere Folgen und Robustheit bei wiederholter Produktion bleiben zu bewerten. Quellen: [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [AMD-Kompatibilitätsmatrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html).

## Geplanter Bedienablauf

Nach der einmaligen Installation und Abo-Anmeldung:

~~~powershell
pla doctor
pla init .\energy-models --topic "Energiebasierte Modelle gründlich verstehen"
pla run .\energy-models --approve-audio
pla status .\energy-models
~~~

Der freigegebene Gesamtlauf umfasst Recherche, Wissensmodell, Serienplanung, Skripte, Prüfungen und fertige Audiodateien. Ohne Audio-Freigabe endet er bei den prüfbaren Skripten. Die Freigabe gilt für diesen Lauf, nicht als dauerhafte Erlaubnis für beliebige spätere Produktionen. Die geprüften und gerenderten Stände werden mit ihren Hashes protokolliert.

Bei ausgeschöpftem Abo-Kontingent speichert das Programm seinen Stand. Ein späteres `pla resume .\energy-models` setzt dort fort. Es gibt keine Schleife, die das Limit ständig erneut anfragt, und keinen stillen Wechsel auf eine kostenpflichtige API. Ein fehlender Login, ein technischer Fehler und ein Qualitätsproblem erhalten unterscheidbare Statusmeldungen.

Die einzelnen Befehle aus der Spezifikation bleiben für gezielte Prüfung und Korrektur verfügbar. Der Nutzer muss keine Texte zwischen Chatfenstern kopieren und keine Audioschnipsel in einem Editor zusammensetzen.

## Reihenfolge und überprüfbare Ergebnisse

| Meilenstein | Sichtbares Ergebnis | Voraussetzung |
| --- | --- | --- |
| 0. Machbarkeit auf Windows | Deutsche Hörprobe mit zwei Stimmen, automatisch fertige MP3 und gemessene Renderdaten; erfolgreicher Abo-CLI-Test | Zugang zum Zielrechner |
| 1. Ausführbares Grundgerüst | Installierbare CLI, Datenverträge, Textadapter, Status und Wiederaufnahme | CLI-Teil aus 0; GPU-unabhängig |
| 2. Kleiner vollständiger Durchlauf | Aus einem lokalen Quelldossier entsteht eine geprüfte und automatisch montierte Audiofolge | 0 und 1 |
| 3. Recherche und fachliche Tiefe | Aus einem Thema entstehen echte Quellen, Wissensmodell, Serienentwurf und eine zentrale Erklärfolge | 2 |
| 4. Zusammenhängende Serie | Mehrere aufeinander aufbauende Folgen mit Abdeckung und inhaltlich begründeten Erweiterungen | 3 |
| 5. Zuverlässige Gesamtproduktion | Vollständige Pilotserie, automatische Fehlerbehandlung, belastbare Wiederaufnahme und Windows-Anleitung | 4 |

### Meilenstein 0: Die riskanten Annahmen zuerst prüfen

Auf dem Windows-Rechner werden Betriebssystemversion, Treiber, GPU-Speicher und passende PyTorch-/AMD-Laufzeit erfasst. Zuerst wird die native Windows-Installation geprüft. WSL2 wäre ein gezielter Ausweichweg, falls der native TTS-Versuch an einer nachgewiesenen Inkompatibilität scheitert; es ist keine vorab vorausgesetzte Installation.

Zwei kleine Versuche liefern belastbare Ausgangsdaten:

1. Codex CLI mit vorhandener Abo-Anmeldung: strukturierte Ausgabe erzeugen, tatsächlich verfügbare Suchwerkzeuge prüfen und eine gefundene Quelle abrufen.
2. Qwen3-TTS: einen deutschen Dialog mit wechselnden und längeren Sprecherpassagen, Fachbegriffen, Zahlen und Einheiten erzeugen. Segmente automatisch zu einer MP3 mit Pausen und Kapitelmarken verbinden.

Gemessen werden Modellladezeit, benötigter GPU-Speicher, Renderdauer im Verhältnis zur Hörzeit sowie Fehler bei wiederholter Erzeugung. Eine Hörprobe bewertet Verständlichkeit, Natürlichkeit, Aussprache und Stimmenkonstanz. Das ist eine Qualitätsentscheidung beim Aufbau, kein manueller Schnittschritt für jede Folge.

**Abnahme:** Ein wiederholbarer Textaufruf und ein dokumentierter lokaler Audioweg funktionieren. Die gewählte Modellvariante und Stimmen werden anhand der Ergebnisse festgehalten. Bei TTS-Problemen werden zunächst kleinere Modellvariante oder kompatible lokale Alternative geprüft. Eine Änderung des Betriebswegs wird mit konkretem Fehler und brauchbarer Alternative zur Entscheidung vorgelegt; bis dahin kann das GPU-unabhängige Grundgerüst entstehen.

### Meilenstein 1: CLI, Datenverträge und wiederaufnehmbare Aufträge

- Python-Paket, Konfiguration und Befehle `init`, `doctor`, `status` und `resume` anlegen.
- Die strukturierten Artefakte aus der Spezifikation als versionierte Schemas umsetzen; mit TopicBrief, SourceDocument, KnowledgeModel, SeriesPlan, EpisodeScript und RunManifest beginnen.
- Den Codex-Adapter für definierte Eingaben, JSON-Schema-Ausgaben, Zeitlimits, Fehlerklassifikation und verfügbare Nutzungsmetadaten bauen.
- Ergebnisse vor Übernahme validieren und atomar speichern. Unvollständige Modellantworten werden nicht zu fertigen Stufenergebnissen.
- Pro Stufe Eingabehash, Prompt- und Modellversion, Ergebnis und Status erfassen. Zustände umfassen `pending`, `running`, `completed`, `waiting_for_quota`, `blocked` und `failed`.
- Bei Wiederaufnahme nur unveränderte, erfolgreich abgeschlossene Ergebnisse übernehmen. Abbruch und erneuter Start dürfen keine abgeschlossene Arbeit duplizieren.

**Abnahme:** Eine kleine strukturierte Aufgabe läuft über das Abo und lässt sich nach einem simulierten Abbruch fortsetzen. Ungültige Ausgabe, fehlender Login und Kontingentlimit werden unterscheidbar behandelt. In normalen Tests werden Providerantworten aufgezeichnet oder simuliert; sie benötigen keine laufenden Abos oder GPU.

### Meilenstein 2: Ein kleiner Durchlauf bis zur fertigen Audiodatei

Mit `fixtures/simple_topic` wird zunächst ein kleiner, fachlich überschaubarer Quellenbestand vollständig verarbeitet:

1. Quellen importieren, in stabile Abschnitte zerlegen und referenzieren.
2. Ein kleines Wissensmodell, einen Folgenplan und ein sprechbares Skript erzeugen.
3. Quellenbindung, Sprecherzuordnung, Vollständigkeit und geschätzte Laufzeit prüfen.
4. Freigegebenes Skript lokal sprechen, automatisch montieren und exportieren.

Das kanonische `script.yaml` enthält geordnete Segmente mit `segment_id`, `scene_id`, `chapter_id`, `speaker_id`, `text`, `knowledge_refs` und `pause_after_ms`. Die Wissensmodell-IDs führen zu Belegen der Form `source_id#section_id`. Nur der gesprochene Text geht an TTS; Regie und Referenzen werden nicht vorgelesen. Die Lesefassung, das Transkript und der Renderauftrag entstehen aus demselben Datenstand.

Lange Sprecherpassagen können für die Synthese an Satzgrenzen unterteilt werden. Sie bleiben inhaltlich zusammenhängende Erklärungen. Die gemessene Audio-Zeitleiste speichert die Zuordnung zwischen Skriptsegmenten, Audiodateien und Kapiteln.

**Abnahme:** Der gesamte Weg bis MP3, Kapitel, Transkript und Show Notes funktioniert ohne manuelle Montage. Ein fehlender Quellenbezug blockiert die Produktion; ein defektes Audiosegment blockiert den finalen Export. Dieser kleine technische Durchlauf ist noch kein Nachweis für eine tiefe Serie.

### Meilenstein 3: Themenrecherche und eine gehaltvolle Erklärfolge

- Aus dem Thema Teilfragen, Voraussetzungen und Suchaufträge ableiten.
- Quellen suchen und tatsächlich abrufen; Zugriffsfehler und Auswahlgründe dokumentieren.
- Markdown, Text, HTML und textbasierte PDFs in referenzierbare Abschnitte importieren. Video- oder Audioquellen zunächst nur bei zugänglichem Transkript verwenden; automatische Transkription fremder Medien bleibt eine spätere Ergänzung.
- Claims, Begriffe, Mechanismen, Beispiele, Gegenpositionen und Unsicherheiten zu einem Wissensmodell verbinden.
- Recherche-Briefing, Argumentkarte, offene Fragen und vorläufigen Serienplan daraus ableiten.
- Eine zentrale Erklärfolge mit zwei Hosts schreiben, prüfen und als Audio produzieren.

Der erste fachliche Pilot ist energiebasiertes Maschinenlernen anhand konkret recherchierter Arbeiten von Yann LeCun und Alfredo Canziani. Eine zentrale Folge muss einen Mechanismus Schritt für Schritt erklären und ein Beispiel durchführen. Sie ist aussagekräftiger als ein reiner Serieneinstieg. Das Blutwerte-Thema kann danach Quellenkritik und Gesundheitskontext prüfen; die Identität einer genannten Person muss dafür belegt werden.

Deterministische Referenzprüfungen und inhaltlicher Modellreview werden getrennt ausgewiesen. Der Review erhält konkrete Quellenabschnitte und muss problematische Passagen benennen. Eine bloße Quellen-ID oder eine lange Wortliste erfüllt den Tiefencheck nicht.

**Abnahme:** Ein Themenauftrag funktioniert ohne vorbereitete Quelldateien. Die zentrale Folge besteht die Kriterien aus [system-quality-assessment.md](system-quality-assessment.md). Wesentliche Recherchelücken bleiben sichtbar und blockieren abhängige Inhalte. Quellen- und Aufruflimits begrenzen einzelne Arbeitsläufe; sie dürfen keine unbegründete Vollständigkeitsbehauptung erzeugen.

### Meilenstein 4: Inhaltlich geplante und erweiterbare Serien

- Teilfragen, Begriffe und Claims einer Abdeckungsmatrix und geordneten Folgen zuweisen.
- Jede Folge erhält relevante Quellen, benötigte Wissensmodelleinträge und den Stand bereits erklärter sowie vertagter Inhalte.
- Skripte folgenweise erzeugen und gezielt automatisch überarbeiten; die Gesamtserie muss nicht in einen einzelnen Modellaufruf passen.
- Voraussetzungen, Fortschritt, Wiederholungen und abschließende Synthese über Folgengrenzen prüfen.
- Laufzeitschätzungen mit den gemessenen Sprechgeschwindigkeiten der gewählten Stimmen kalibrieren.
- Bei zusätzlichem Erklärbedarf Folgen ergänzen. Überschreitet eine fertige Folge 30 Minuten, Inhalte an einer sinnvollen Grenze aufteilen, Übergänge überarbeiten und betroffene Prüfungen erneuern.
- Neue Erkenntnisse zuerst ins Wissensmodell aufnehmen und nur die davon abhängigen Ergebnisse neu erzeugen.

**Abnahme:** `fixtures/mechanism_series` wächst bei zusätzlichem inhaltlichem Bedarf über seinen ursprünglichen Plan hinaus. Frühere Grundlagen werden passend aufgegriffen; vertagte Kernfragen gehen nicht verloren. `fixtures/conflicting_perspectives` prüft die Trennung von Position, Interpretation und belegter Aussage. Keine feste Gesamtstundenzahl dient als Qualitätsmaß.

### Meilenstein 5: Automatische Produktion belastbar machen

Die fertige Produktion übernimmt folgende Arbeit selbst:

| Schritt | Verhalten |
| --- | --- |
| Sprechtext vorbereiten | Zahlen, Einheiten, Abkürzungen und Aussprache zentraler Begriffe eindeutig behandeln; Bedeutung beibehalten |
| Segmente erzeugen | Beständige Stimmen verwenden; Text, Modellrevision, Stimme, Aussprache und Einstellungen im Cache-Key berücksichtigen |
| Audio prüfen | Fehlende oder beschädigte Dateien, leeres Audio, auffällige Stille, Pegelfehler und unplausible Dauer erkennen |
| Fehler korrigieren | Betroffene Segmente mit begrenzten Versuchen neu erzeugen; verbleibende Fehler klar melden |
| Folge montieren | Formate vereinheitlichen, geplante Pausen einsetzen und Segmente ohne abgeschnittene Sprachlaute verbinden |
| Ausgabe messen | Lautheit normalisieren, tatsächliche Dauer und Kapitelpositionen ermitteln |
| Exportieren | MP3 mit 44,1 kHz, Stereo und Ziel -16 LUFS sowie Kapitel, Transkript und Show Notes bereitstellen |

FFmpeg liefert unter anderem Lautheitsnormalisierung und Stilleerkennung; die Anwendung verbindet diese Werkzeuge mit der strukturierten Regie. [FFmpeg-Filterdokumentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)

Ergänzende lokale Rücktranskription wird am Pilot darauf geprüft, ob sie Auslassungen und Wiederholungen zuverlässig erkennt. Sie ist kein Beweis für fehlerfreie Aussprache. Ein automatischer Befund muss das betroffene Segment und den Grund benennen. Nach ausgeschöpften Reparaturversuchen endet der betroffene Auftrag mit Fehlerbericht; er fordert keinen manuellen Audioschnitt an.

Unterbrechungen werden auf Stufen-, Folgen- und Segmentebene abgefangen. Bereits gültige Audiodateien bleiben im Cache. Kapitelzeiten stammen aus den gemessenen, fertig montierten Audiodaten. Eine Aufteilung oder Textkorrektur erneuert die betroffenen Checks und Ausgabedateien.

**Abnahme:** Die vollständige Pilotserie wird unter Windows mit Abo-Textbackend und lokaler Sprachausgabe erzeugt. Installation, Login, Start, Fortschritt, Wiederaufnahme und Fehlerbehebung sind dokumentiert. Eine redaktionelle Hörprüfung bewertet die Qualität des MVP; im normalen Produktionsablauf ist keine manuelle Bearbeitung jeder Folge erforderlich.

## Gezielte Verifikation

Diese Prüfungen adressieren die wesentlichen Risiken:

- Unbelegte Aussage oder nicht eingelesene Quelle: Prüfung schlägt fehl.
- Formal gültiges, aber oberflächliches Skript: Tiefenreview nennt fehlende Erklärschritte.
- Änderung an einer Quelle: abhängige Prüfungen und Skripte werden als veraltet erkannt.
- Abbruch während eines Modellaufrufs: kein unvollständiges Ergebnis wird übernommen.
- Kontingentlimit: Stand bleibt erhalten, Wiederaufnahme wiederholt keine fertigen Stufen.
- Defektes Audiosegment: gezielte Neuerzeugung statt vollständiger Neuberechnung der Serie.
- Tatsächlich zu lange Folge: sinnvolle Aufteilung, erneute Prüfung und aktualisierte Kapitel.
- Zusätzliche inhaltliche Anforderungen: weitere Folgen möglich, ohne pauschales Serienlimit.

Die technische Testsuite läuft ohne bezahlte Modellaufrufe. Echte Abo-, GPU- und Hörtests sind getrennte Pilotprüfungen auf dem Zielrechner. Ein bestandener Modellreview allein ist kein Nachweis für fachliche Richtigkeit oder angenehme Hörqualität.

## Noch zu klären und nächster Schritt

Die Produktrichtung und das Zielbetriebssystem sind entschieden. Für den Beginn ist keine weitere Grundsatzentscheidung des Nutzers nötig.

| Punkt | Stand / nächster Nachweis |
| --- | --- |
| Exakte Windows-, Treiber- und Laufzeitversionen | Im Windows-Versuchsprotokoll erfasst |
| Qwen-Modellvariante und zwei geeignete Stimmen | 0.6B-CustomVoice erprobt; Aiden und Vivian nach deutschem und englischem Stimmenvergleich gewählt |
| Verfügbare Suche im gewählten Abo-CLI | Echte Suchereignisse und sechs lokal eingelesene Quellen nachgewiesen |
| Verständliche Erklärweise | Ohne Vorwissen, mit zusammenhängenden mentalen Bildern und erklärten Grenzen; im späteren Hördialog prüfen |
| Nutzen automatischer Rücktranskription | Mit bekannten Fehlerfällen aus dem Audiopilot bewerten |

`pla script` verarbeitet das vorhandene Recherchedossier zu einem kompakten Wissensmodell, einem Serienentwurf und belegten Dialogskripten. Der Nutzer liest zunächst die erste Folge mit Aiden und Vivian. Audio wartet ausdrücklich auf seinen anschließenden Auftrag. Die Verbindung geprüfter Skripte mit der Audio-Produktion sowie der vollständige Review über alle Folgengrenzen sind die nächsten Ausbauschritte. Offene Recherchefragen bleiben sichtbar und dürfen nicht durch erfundene Details geschlossen werden. Claude-Unterstützung, zusätzliche Audioanbieter und weitere Oberflächen folgen nur bei konkretem Bedarf.
