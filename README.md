# Podcast Automate: Deep-Dive-Serien

Ein persönliches Recherche-zu-Podcast-System: Ein Thema vorgeben und daraus eine zusammenhängende, quellengebundene Deep-Dive-Serie entwickeln. Themenumfang und gewünschte Tiefe bestimmen, wie viele Folgen nötig sind. Die Gesamtdauer und Folgenzahl haben keine feste Vorgabe; einzelne Folgen dauern höchstens 30 Minuten.

Die Erklärungen setzen kein Fach- oder Mathematikwissen voraus. Vertraute Situationen, klare mentale Bilder und Schritt-für-Schritt-Beispiele machen die Zusammenhänge hörbar. Nötige Fachbegriffe werden erst nach der Idee erklärt; Metaphern erhalten eine verständliche Grenze.

## Geführte Erstellung im Browser

**`Podcast-Studio.cmd` doppelklicken.** Das lokale [Podcast Studio](http://127.0.0.1:8765) führt durch Idee, Stimmenwahl, Recherche, Inhaltsverzeichnis, Skriptprüfung und Audio. Das Serverfenster bleibt geöffnet. Alternativ startet `.\.venv\Scripts\pla.exe studio` dieselbe Oberfläche.

Du kannst deine Wünsche mit Codex oder OpenRouter besprechen, den vorgeschlagenen Auftrag bearbeiten und das Inhaltsverzeichnis vor dem Schreiben freigeben. Das fertige Skript liest du vor einer getrennten Audio-Freigabe. **Für Audio wählst du unabhängig davon lokales Qwen oder Gemini 3.1 Flash TTS über OpenRouter mit seinen 30 Stimmen.** Bei Gemini sind Sadaltager und Aoede vorbelegt. **Alle 30 Hörproben lassen sich je Sprache einmal erzeugen und danach über ▶ Play neben jeder Stimme vergleichen.** Die gemeinsame Bibliothek bleibt über Projektwechsel und Neustarts erhalten; Abspielen braucht keinen API-Key und erzeugt keine neuen Aufnahmen. Bestehende Skripte können ohne Neuschreiben mit dem anderen Anbieter vertont werden. Fortschritt, Anhalten, Fortsetzen und MP3-Download sind in derselben Oberfläche erreichbar. Die belegte Web-Recherche verwendet derzeit weiterhin Codex. API-Keys bleiben nur für die Sitzung im Speicher. [Gemini-Audio einrichten und Grenzen](docs/gemini-audio.md).

[Studio starten, Ablauf und Grenzen](docs/studio.md).

Der Fortschrittsbereich zeigt die aktuelle Folge, den laufenden Modellschritt und die Zahl fertiger Folgen in dieser Stufe. Geprüfte Lehrkonzepte lassen sich dort direkt aufklappen und lesen, während weitere Folgen entstehen. Die Anzeige aktualisiert sich automatisch; ein einzelner Modellaufruf kann mehrere Minuten dauern. Das gewählte Projekt bleibt beim Neuladen über die Seitenadresse erhalten.

Interne Überarbeitungen des Lehrkonzepts laufen nach der Freigabe des Inhaltsverzeichnisses automatisch. Nach zwei allgemeinen Überarbeitungen werden verbleibende Erklärungslücken einmal gezielt ergänzt und erneut unabhängig geprüft. Dafür musst du nicht auf „Fortsetzen“ klicken. Offene Qualitätsmängel oder ausgeschöpfte Limits stoppen den Auftrag weiterhin mit einer konkreten Meldung; Freigaben für Inhaltsverzeichnis und Audio bleiben eigene Entscheidungen.


## Ausführbare Werkzeuge: Version 0.1

Das Studio verbindet Projektverwaltung, Quellenrecherche, Inhaltsverzeichnis, Lehrplanung, Dialog-Polishing, Qualitätsprüfung und freigegebene Vertonung. Die folgenden Einzelbefehle dienen zusätzlich der gezielten Arbeit und Fehlersuche. [Installation und erste Proben unter Windows 11](docs/windows-quickstart.md).

| Befehl | Bereits implementiert |
| --- | --- |
| `pla studio` | Geführte Podcast-Erstellung im lokalen Browser öffnen |
| `pla init` | Validierten Themenauftrag und lokale Projektstruktur anlegen |
| `pla doctor` | Installation, Abo-Anmeldung und TTS-Umgebung prüfen |
| `pla text-probe` | Strukturierte Codex-Antwort über die bestehende Abo-Anmeldung anfordern |
| `pla research` | Live suchen, HTML/PDF/Text tatsächlich abrufen und ein Dossier mit geprüften Quellenreferenzen erstellen |
| `pla script --episode ep_001` | Aus dem Dossier einen Serienentwurf und das geprüfte Dialogskript der ersten Folge erstellen |
| `pla audio --episode ep_001 --approve-audio` | Ein gelesenes und freigegebenes Skript vertonen und als MP3 exportieren |
| `pla audio-probe --approve-audio` | Mitgelieferten deutschen oder englischen Dialog lokal sprechen und automatisch montieren |
| `pla status` | Fortschritt, Fehler und veränderte Ergebnisse anzeigen |
| `pla resume` | Recherche, Skripte oder Proben mit gültigen Ergebnissen fortsetzen |
| `pla schemas` | Die implementierten Datenverträge als JSON-Schemas exportieren |

Der lokale Audioweg erzeugt MP3, Kapitel, Transkript und Messberichte. Die Montage ist mit echten FFmpeg-Aufrufen getestet; die Modellantworten werden in den automatisierten Tests simuliert. Beim [Windows-Versuch am 13.09.2026](docs/windows-pilot.md) bestanden zusätzlich echte Codex-Aufrufe, lokale Qwen-Hörproben und ein Recherchelauf mit abgerufenen Quellen. Als Stimmen für weitere Folgen sind Aiden und Vivian gewählt. `pla script` verbindet das Dossier mit einem belegten Dialog zur Leseprüfung. Vollständige Serienproduktion und fachliche Hörabnahme bleiben weitere Ausbauschritte.

## Vom Thema zum Recherchedossier

Nach Aktualisierung der Controller-Installation:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pla.exe research .\projects\windows-pilot
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
```

`research` verwendet das bestehende ChatGPT-Abo für echte Websuche. Die Anwendung lädt die ausgewählten Quellen selbst, speichert Rohdateien und referenzierbare Textabschnitte, erstellt ein Dossier und prüft dessen Belege. Ein fehlgeschlagener Abruf wird als Lücke protokolliert. Das Ergebnis steht unter `research/research_briefing.md`; mit `pla resume` kann derselbe unveränderte Auftrag fortgesetzt werden. Dieser Befehl erzeugt ein Dossier, noch keine Podcastfolge. [Recherche bedienen, Ergebnisse und Grenzen](docs/research.md).

## Das erste Skript lesen

```powershell
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --episode ep_001
```

Der Dialog steht anschließend in `projects/windows-pilot/episodes/ep_001/script.md`, der Serienentwurf in `research/series_outline.md`. Der Befehl prüft Quellenbezüge, Erklärweise und Aufbau. **Zuerst das Skript lesen; Audio folgt erst nach dem ausdrücklichen Auftrag dazu.** [Skriptworkflow und Ergebnisse](docs/scripts.md).

Für einzelne Skriptläufe lässt sich OpenRouter mit `--backend openrouter --model "anbieter/modell-id" --api-key` wählen. `--api-key` ohne Wert fragt den Key verdeckt für diesen Aufruf ab. Alle Qualitätsprüfungen bleiben aktiv; OpenRouter wird über API-Guthaben abgerechnet. [Anbieterwahl, Key-Übergabe und Wiederaufnahme](docs/scripts.md#openrouter-für-einen-skriptlauf).

Neue Skriptläufe enthalten vor dem Schreiben eine eigene Lehrplanung: Einstieg, Voraussetzungen, Lernziele, durchgearbeitetes Beispiel und Synthese werden gegen die Quellen geprüft. Danach prüfen ein separater Leseraufruf, eine unabhängige redaktionelle Sicht auf den gesprochenen Text und eine mit Textstellen belegte Lehrprüfung, ob der Dialog diese Ziele tatsächlich entwickelt. Fehlende Erklärgrundlagen recherchiert das Studio automatisch mit Codex nach, prüft die zusätzlichen Belege und setzt den freigegebenen Plan ohne weiteren Klick fort. Bleibt die Lücke offen, zeigt es die konkreten Fragen; fortbestehende Lehrmängel blockieren den Export. [Produktionsworkflow und Grenzen](docs/teaching-design.md), [Regressionstests mit echten Modellaufrufen](evals/teaching_quality/README.md).

Die Erklärungen sind deutsch; etablierte Fachbegriffe bleiben englisch (etwa Query, Key, Value und Attention) und werden bei Bedarf kurz erklärt. Das gilt durchgängig bis zum Polishing und den Prüfberichten.

Zwischen Fachentwurf und abschließendem Review liegt ein eigener **Dialog-Polishing-Schritt**. Er arbeitet gesprochene Sprache und die Rollen aus: Host A erklärt ruhig und präzise; Host B hinterfragt, denkt mit und verbindet Details mit ihrer Bedeutung. Längere Monologe bleiben erlaubt. Ein separater Vorher-/Nachher-Vergleich prüft, ob Fakten, Begründungen und Einschränkungen erhalten geblieben sind. [Polishing, Rollen und gespeicherte Vergleiche](docs/scripts.md#eigener-dialog-polishing-schritt).

## FFmpeg lokal installieren und Windows-Probe starten

FFmpeg und ffprobe werden projektlokal unter `tools/ffmpeg/bin/` installiert. Das Setup lädt den Windows-x64-Essentials-Build von [gyan.dev](https://www.gyan.dev/ffmpeg/builds/), einem auf der [FFmpeg-Downloadseite](https://ffmpeg.org/download.html) verlinkten Anbieter, und prüft die festgehaltene SHA-256-Prüfsumme. Version 9.0.1 ist im Setup festgelegt. Die Binärdateien, mitgelieferte Dokumentation und lokale Download-Metadaten sind über `.gitignore` ausgeschlossen; nach einem frischen Git-Checkout wird das Setup erneut ausgeführt.

In PowerShell im Repository-Verzeichnis:

```powershell
powershell -NoProfile -File .\scripts\setup-ffmpeg.ps1
$ffmpegBin = (Resolve-Path .\tools\ffmpeg\bin).Path
$env:PATH = "$ffmpegBin;$env:PATH"
ffmpeg -version
ffprobe -version
```

Die beiden PATH-Zeilen gelten für das aktuelle PowerShell-Fenster und müssen in einem neuen Fenster erneut ausgeführt werden. Das Setup verändert den systemweiten PATH nicht. Erneutes Ausführen prüft die bereits installierten Binärdateien und überspringt einen unnötigen Download.

Der aktuelle Meilenstein ist der **erste echte Versuch auf Windows 11 mit der Radeon RX 9070 XT**: Abo-Verbindung prüfen, Qwen in einer separaten Python-Umgebung einrichten und den deutschen Testdialog mit zwei Stimmen automatisch montieren. Nach der [Windows-Einrichtung](docs/windows-quickstart.md) und dem Anlegen von `projects/energy-models`:

```powershell
.\.venv\Scripts\pla.exe doctor .\projects\energy-models --json
.\.venv\Scripts\pla.exe text-probe .\projects\energy-models
.\.venv\Scripts\pla.exe audio-probe .\projects\energy-models --approve-audio
.\.venv\Scripts\pla.exe status .\projects\energy-models
```

Die Hörprobe bewertet Aussprache, Stimmenkonstanz und Natürlichkeit; der TTS-Bericht erfasst Modellrevision, GPU-Speicher und Renderzeiten. Für die automatische Montage lassen sich bereits ohne Qwen und Modellkonto alle Tests einschließlich FFmpeg ausführen:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Ergebnis auf dem Zielrechner am 13.09.2026: **101 Tests bestanden, keine übersprungen**, einschließlich Sprachwahl, Recherche, Quellenprüfung, Lehrplanung, Dialog-Polishing, OpenRouter-Anbindung und Wiederaufnahme. OpenRouter ist dabei mit simulierten API-Antworten geprüft; ein echter API-Aufruf steht aus. Zusätzlich wurden die Lehrprüfungen mit drei echten Codex-Modellfällen erprobt: Der vom Nutzer abgelehnte Pilot und ein oberflächlicher Kontrolltext wurden abgewiesen, eine ausgearbeitete Einführung wurde angenommen. Ein echter Polishing-Test nahm einen überarbeiteten Pilotausschnitt an und wies eine absichtlich verfälschte Kontrollfassung zurück. [Prüfergebnisse und ihre Grenzen](docs/quality-verification.md). Der echte Text-, Recherche- und Audioversuch liegt lokal unter `projects/windows-pilot/`; `doctor` bestätigt auch die Qwen-/PyTorch-Umgebung. Der ursprüngliche deutsche Dialog erzeugte 58,79 Sekunden MP3; reine Synthesezeit etwa 5 Minuten 35 Sekunden bei knapp 3 GiB belegtem GPU-Speicher. [Versuchsprotokoll und lokale Ergebnisse](docs/windows-pilot.md).

Für eine englische Hörprobe unterstützt `project.yaml` auch `language: en-US`; der Standard bleibt `de-DE`. Die lokale Vergleichskonfiguration liegt unter `projects/windows-pilot-en/`. [Sprachwahl und Vergleich mit denselben Stimmen](docs/qwen-windows.md#deutsch-und-englisch-vergleichen).

Alle neun eingebauten Qwen-Stimmen lassen sich mit `scripts/generate-voice-samples.py` auf Englisch und Deutsch vergleichen. [Erzeugung und lokaler Hörindex](docs/qwen-windows.md#alle-neun-stimmen-anhören).

Die Qwen-Einrichtung lässt sich für ein angelegtes Projekt wiederholen:

```powershell
powershell -NoProfile -File .\scripts\setup-qwen.ps1 -ProjectDir .\projects\windows-pilot
```

Das Setup verwendet `.venv-tts`, AMD-PyTorch und eine feste Modellrevision. Die vorhandene Lemonade-Installation 10.6.0 bietet keinen Qwen-TTS-Backend-Eintrag; die Anwendung nutzt deshalb ihren eigenen lokalen Worker. [Qwen-Versionen und Windows-Einrichtung](docs/qwen-windows.md).

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
7. Nach Audio-Freigabe mit lokalem Qwen oder Gemini über OpenRouter sprechen, automatisch montieren und MP3-Folgen mit Kapiteln, Transkripten und Show Notes exportieren.

Audio-Export gehört zum MVP. Ein Lauf kann zur Prüfung bei den Skripten enden; Audio wird ausschließlich mit expliziter Freigabe und bestandenen blockierenden Qualitätsprüfungen erzeugt.

Tutor-Modus, Quiz, Karteikarten, Prüfungsmodus und Wiederholungsplanung sind spätere Optionen und keine MVP-Anforderungen.

## Betrieb

Das Zielsystem ist Windows 11 mit einer AMD Radeon RX 9070 XT. Die belegte Recherche verwendet Codex CLI mit dem vorhandenen ChatGPT-Abo. Für Redaktion und Skripte ist zusätzlich OpenRouter wählbar; die Audioauswahl zwischen lokalem Qwen und Gemini über OpenRouter ist davon unabhängig. Qwen3-TTS läuft im technischen Pilot lokal auf der Radeon; Hörqualität und Eignung für lange Folgen sind noch zu bewerten. Die technischen Grundlagen und Quellen stehen im [Implementierungsplan](docs/personal-learning-podcast-system-plan.md).

Sprecherwechsel, Pausen, Montage, Lautheitsanpassung, Kapitel und Export werden automatisiert. Manueller Audioschnitt gehört nicht zum Bedienablauf. Zusätzliche bezahlte APIs sind keine Voraussetzung des MVP. Bei ausgeschöpftem Abo-Kontingent pausiert der Lauf und bewahrt bereits fertige Ergebnisse.

## Weitere Entwicklung

Ein einzelner Befehl `pla run` für die gesamte Pipeline ist noch nicht implementiert. Die geführte Erstellung verwendet heute das Studio mit getrennten Freigaben für Inhaltsverzeichnis und Audio. Weitere Automatisierung und die Abnahme vollständiger Serien sind in [SPEC.md](SPEC.md) und im [Implementierungsplan](docs/personal-learning-podcast-system-plan.md) beschrieben.

## Entwicklungsstand

Das Grundgerüst, technische Proben, der Rechercheweg bis zum belegten Dossier sowie Serienentwurf und Dialogskripte sind implementiert. Der Code verwendet Python ab 3.12, argparse, Pydantic, YAML und pypdf mit Font-Unterstützung. Die Tests prüfen unter anderem Abo-Pausen, Quellenabruf, Belegvalidierung, Skriptprüfung, Wiederaufnahme und Audio-Montage. Die Vertonung freigegebener Skripte ist implementiert, einschließlich lokalem Qwen, Gemini über OpenRouter und wiederverwendbaren Hörproben. Die fachliche Hörabnahme und die Prüfung vollständiger Serien bleiben weitere Arbeitsschritte.

## Dokumentation

- [docs/studio.md](docs/studio.md): Browseroberfläche, Anbieterwahl, Freigaben und Fortsetzung.
- [docs/gemini-audio.md](docs/gemini-audio.md): Gemini-Stimmen, gemeinsame Hörprobenbibliothek und OpenRouter-Anbindung.
- [docs/windows-quickstart.md](docs/windows-quickstart.md): ausführbare Befehle, Windows-Installation und aktueller Funktionsumfang.
- [docs/windows-pilot.md](docs/windows-pilot.md): Ergebnisse des ersten echten Versuchs auf dem Windows-Zielrechner.
- [docs/qwen-windows.md](docs/qwen-windows.md): separate Qwen-/AMD-Umgebung, feste Versionen und Hörprobe.
- [docs/research.md](docs/research.md): echte Recherche, Quellenimport, belegtes Dossier, Limits und Wiederaufnahme.
- [docs/scripts.md](docs/scripts.md): Serienentwurf, quellengebundene Dialoge und Leseprüfung vor Audio.
- [docs/teaching-design.md](docs/teaching-design.md): verbindliche Lehrplanung und Prüfungen für neue Skriptläufe.
- [docs/quality-verification.md](docs/quality-verification.md): automatisierte Tests, echte Modellprüfungen und verbleibende Grenzen.
- [SPEC.md](SPEC.md): verbindlicher Hauptfall, Architektur, Datenverträge und Abnahmekriterien.
- [docs/personal-learning-podcast-system-plan.md](docs/personal-learning-podcast-system-plan.md): technische Startentscheidungen, sechs Meilensteine und konkrete Abnahmen.
- [docs/system-quality-assessment.md](docs/system-quality-assessment.md): Bewertungskriterien für Recherche, Tiefe, Serienaufbau und Hörqualität.

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Siehe [LICENSE](LICENSE).
