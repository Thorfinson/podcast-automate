# Windows 11: erster ausführbarer Stand

Version 0.1 enthält Projektverwaltung, eine Codex-Abo-Verbindungsprobe, echte Themenrecherche mit Quellenabruf und Dossier sowie eine automatisch montierte lokale Qwen-Hörprobe. Die Verbindung vom Dossier zur Podcastserie folgt in den nächsten Meilensteinen. [Recherche starten und fortsetzen](research.md).

## 1. Anwendung installieren

Voraussetzungen für das Grundgerüst: Python 3.12 und das heruntergeladene Repository. Die Befehle werden in PowerShell im Repository-Verzeichnis ausgeführt. Eine Aktivierung der virtuellen Umgebung ist nicht nötig.

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pla.exe --help
.\.venv\Scripts\pla.exe init .\projects\energy-models --topic "Energiebasierte Modelle gründlich verstehen" --tts-python .\.venv-tts\Scripts\python.exe
~~~

Die TTS-Umgebung wird in Schritt 3 eingerichtet. Der Projektordner enthält bereits ihre geplante Python-Adresse. Ein erneutes init überschreibt kein vorhandenes Projekt.

## 2. Abos prüfen: Codex und Claude

Codex CLI installieren und mit dem vorhandenen ChatGPT-Konto anmelden. Die Anwendung verwendet den offiziellen CLI-Anmeldestatus; sie liest keine Zugangstokens aus. [Codex-Anmeldung](https://learn.chatgpt.com/docs/auth)

~~~powershell
codex login
.\.venv\Scripts\pla.exe text-probe .\projects\energy-models
.\.venv\Scripts\pla.exe status .\projects\energy-models
~~~

Als zweites Abo kann Claude Code mit einem Claude-Max-Abo dienen. Nach der Installation mit `claude auth login` über claude.ai anmelden; `claude auth status --json` muss `authMethod: "claude.ai"` zeigen. Eine API-Key-Anmeldung wird abgewiesen, weil sie einzeln abrechnen würde. Geprüft ist Claude Code 2.1.92.

~~~powershell
claude auth login
claude auth status --json
.\.venv\Scripts\pla.exe quota
.\.venv\Scripts\pla.exe text-probe .\projects\energy-models --backend claude_code
~~~

`pla quota` zeigt ohne Modellaufruf das Codex-Fenster in Prozent mit Reset-Zeitpunkt und den Claude-Stand (Anmeldung, gegebenenfalls vermerkte Sperre). Im Studio und mit `--backend auto` wird vor jedem Modellaufruf so entschieden: Codex, solange es Kontingent hat, sonst Claude, sonst Pause bis zum frühesten Reset.

Die Probe übergibt das Thema an Codex und erwartet validiertes JSON mit möglichen Vertiefungsfragen. Sie prüft die Anbindung und verbraucht Abo-Kontingent. Sie führt noch keine Recherche durch. Ergebnis und verfügbare Nutzungsmetadaten liegen unter probes/text/<run_id>/.

Die Anbindung verwendet codex exec mit JSON-Ereignissen und einem Ausgabeschema. API-Key-Anmeldung wird abgewiesen; API-Key-Umgebungsvariablen werden nicht an den Kindprozess übergeben. Bei ausgeschöpftem Kontingent bleibt der Lauf mit waiting_for_quota gespeichert. [Nichtinteraktive Codex-Aufrufe](https://learn.chatgpt.com/docs/non-interactive-mode)

## 3. Lokale Sprachausgabe vorbereiten

FFmpeg und ffprobe werden für die Montage verwendet. Das Repository enthält ein Setup für den projektlokalen Windows-x64-Build 9.0.1 mit SHA-256-Prüfung:

~~~powershell
powershell -NoProfile -File .\scripts\setup-ffmpeg.ps1
$ffmpegBin = (Resolve-Path .\tools\ffmpeg\bin).Path
$env:PATH = "$ffmpegBin;$env:PATH"
ffmpeg -version
ffprobe -version
~~~

Die Binärdateien liegen unter `tools/ffmpeg/bin/` und sind von Git ausgeschlossen. Das Setup ist nach einem frischen Checkout erneut nötig. Die beiden PATH-Zeilen in jedem neuen PowerShell-Fenster vor `doctor`, `audio-probe`, `resume` oder den Audio-Tests ausführen; eine systemweite PATH-Änderung ist nicht erforderlich. [Downloadquelle und Details](../README.md#ffmpeg-lokal-installieren-und-windows-probe-starten).

Für Qwen gibt es nun ein Setup für die separate Python-3.12-Umgebung und die AMD-Wheels:

~~~powershell
powershell -NoProfile -File .\scripts\setup-qwen.ps1 -ProjectDir .\projects\energy-models
~~~

Das Setup lädt bei Bedarf Python 3.12.14 nach `tools/python/`, installiert PyTorch 2.9.1 mit ROCm 7.2.1 und Qwen 0.1.1 in `.venv-tts`, prüft GPU-Berechnung und lädt die feste Qwen-Modellrevision. Anschließend aktualisiert es den Python-Pfad und Modellstand in `project.yaml`. Auf dem Zielrechner erkennt diese Kombination die RX 9070 XT und besteht FP32-, FP16- und BF16-Berechnungen. [Versionen, Voraussetzungen und Details](qwen-windows.md).

Anschließend:

~~~powershell
.\.venv\Scripts\pla.exe doctor .\projects\energy-models --json
~~~

doctor zeigt die Pakete der TTS-Umgebung, die von PyTorch erkannte GPU und gegebenenfalls die HIP-Version. Der Befehl lädt keine Modellgewichte und führt keine Spracherzeugung aus. Er prüft auch die Codex-Anmeldung; die Audio-Probe selbst benötigt keine Codex-Anmeldung.

Für die erste Probe sind Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice und die Stimmen Ryan und Serena voreingestellt. Diese Stimmen können Deutsch sprechen; wie passend sie klingen, entscheidet die Hörprobe. Das Modell wird beim ersten Audiolauf von Hugging Face geladen und danach lokal wiederverwendet. Der konkrete Modellstand wird über einen Snapshot festgehalten. [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)

Der Worker verwendet zunächst eager attention und setzt keine FlashAttention-Installation voraus. Die Einstellung cuda:0 ist auch bei PyTorch mit AMD/HIP der verwendete Gerätename. Ein fehlendes GPU-Gerät führt zu einem Fehler; es wird nicht unbemerkt auf CPU umgeschaltet. [PyTorch-Dokumentation zu HIP](https://github.com/pytorch/pytorch/blob/main/docs/source/notes/hip.rst)

## 4. Hörprobe erzeugen

~~~powershell
.\.venv\Scripts\pla.exe audio-probe .\projects\energy-models --approve-audio
~~~

Der Befehl erzeugt den mitgelieferten deutschen Testdialog, setzt die Sprechersegmente und Pausen zusammen, normalisiert die Lautheit und erzeugt:

| Datei unter probes/audio/<run_id>/ | Inhalt |
| --- | --- |
| audio.mp3 | Automatisch montierte Hörprobe |
| chapters.json | Kapitel mit gemessenen Zeitpositionen |
| timeline.json | Zuordnung zwischen Skriptsegmenten und Audiozeiten |
| transcript.md | Gesprochener Text des Testdialogs |
| audio_report.json | Technische Audio-Messdaten und Zielparameter |

Der TTS-Bericht unter runs/<run_id>/tts_report.json enthält Modellrevision, Paketversionen, GPU-Daten, Ladezeit und Renderzeiten der Segmente. Er zeigt auch, welche Segmente aus dem Cache kamen.

Die Ausgabe ist eine technische Probe. Sie enthält keine fachlich geprüfte Podcastfolge. Bitte bei der ersten Hörprobe auf Aussprache, Natürlichkeit und gleichbleibende Stimmen achten. Manueller Schnitt ist nicht erforderlich.

## 5. Fortschritt, Wiederaufnahme und Einstellungen

In einem zweiten PowerShell-Fenster lässt sich der laufende Auftrag prüfen:

~~~powershell
.\.venv\Scripts\pla.exe status .\projects\energy-models
~~~

Nach Abbruch, technischem Fehler oder Abo-Pause:

~~~powershell
.\.venv\Scripts\pla.exe resume .\projects\energy-models
~~~

resume übernimmt die Freigabe einer unveränderten Audio-Probe. Fertige, unveränderte Stufen und Audiosegmente werden wiederverwendet. Fehlende oder geänderte Ergebnisse werden erkannt. Ein vollständig erledigter, unveränderter Lauf erzeugt bei resume keine neuen Modellaufrufe. Mit --run-id kann ein älterer Lauf ausgewählt werden.

Einstellungen stehen in project.yaml. Dort können beispielsweise runtime.tts_model, runtime.tts_python und voice_profile angepasst werden. Für eine andere Modellvariante, Stimme oder sonstige geänderte Eingaben wird eine neue Probe gestartet; ein alter Lauf wird damit nicht still weitergeführt. Die Variante 1.7B-CustomVoice kann nach erfolgreichem ersten Test für einen Qualitätsvergleich gewählt werden.

Rückgabecodes: 0 bedeutet erfolgreich, 1 bedeutet blockiert oder fehlgeschlagen, 2 bedeutet Kontingentpause und 130 bedeutet Abbruch. Alle Befehle unterstützen --json für maschinenlesbare Ergebnisse.

## Verifikation und Grenzen dieser Version

Die Tests verwenden simulierte Codex-Antworten und echte FFmpeg-Montage mit erzeugten Testsignalen. Sie benötigen weder ein Modellkonto noch eine GPU.

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Ohne FFmpeg werden die Audio-Integrationstests ausdrücklich übersprungen. Die Kernprüfungen laufen weiter.

Lokal geprüft sind Dateiverwaltung, Schemas, Prozessaufrufe, Kontingentzustände, Wiederaufnahme, Audio-Montage, Laufzeiten und Kapitel. Beim [Windows-Versuch am 13.09.2026](windows-pilot.md) bestanden alle 24 Tests mit FFmpeg, ein echter Codex-Abo-Aufruf und eine vollständige Qwen-Hörprobe auf der RX 9070 XT einschließlich Wiederaufnahme. Die 58,79 Sekunden lange MP3 ist technisch geprüft; Aussprache und Natürlichkeit sind noch anzuhören. Für Windows und Linux ist ein CI-Testlauf ohne Modellkonten eingerichtet.

`pla research` implementiert einen begrenzten Live-Recherchepass mit Quellenimport, Abschnittsreferenzen, Dossier, Referenzprüfung und Modellreview. Danach erstellt `pla script .\projects\windows-pilot --episode ep_001` einen Serienentwurf und die erste belegte Dialogfolge zur Leseprüfung. [Skriptworkflow](scripts.md). Audio wartet auf den ausdrücklichen Auftrag nach dieser Prüfung.

Vollständige Qualitätsprüfungen über alle Folgengrenzen, Audio-Produktion recherchierter Folgen und Aufteilung zu langer Folgen bleiben weitere Ausbauschritte. `run`, `ingest`, `model`, `plan`, `check`, `render` und `export` aus der vollständigen Produktspezifikation werden noch nicht als eigenständige fertige CLI-Funktionen angeboten.
