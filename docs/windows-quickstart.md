# Windows 11: erster ausführbarer Stand

Version 0.1 enthält Projektverwaltung, eine Codex-Abo-Verbindungsprobe und eine automatisch montierte lokale Qwen-Hörprobe. Die vollständige Recherche- und Serienpipeline folgt in den nächsten Meilensteinen.

## 1. Anwendung installieren

Voraussetzungen für das Grundgerüst: Python 3.12 und das heruntergeladene Repository. Die Befehle werden in PowerShell im Repository-Verzeichnis ausgeführt. Eine Aktivierung der virtuellen Umgebung ist nicht nötig.

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pla.exe --help
.\.venv\Scripts\pla.exe init .\projects\energy-models --topic "Energiebasierte Modelle gründlich verstehen" --tts-python .\.venv-tts\Scripts\python.exe
~~~

Die TTS-Umgebung wird in Schritt 3 eingerichtet. Der Projektordner enthält bereits ihre geplante Python-Adresse. Ein erneutes init überschreibt kein vorhandenes Projekt.

## 2. Codex-Abo prüfen

Codex CLI installieren und mit dem vorhandenen ChatGPT-Konto anmelden. Die Anwendung verwendet den offiziellen CLI-Anmeldestatus; sie liest keine Zugangstokens aus. [Codex-Anmeldung](https://learn.chatgpt.com/docs/auth)

~~~powershell
codex login
.\.venv\Scripts\pla.exe text-probe .\projects\energy-models
.\.venv\Scripts\pla.exe status .\projects\energy-models
~~~

Die Probe übergibt das Thema an Codex und erwartet validiertes JSON mit möglichen Vertiefungsfragen. Sie prüft die Anbindung und verbraucht Abo-Kontingent. Sie führt noch keine Recherche durch. Ergebnis und verfügbare Nutzungsmetadaten liegen unter probes/text/<run_id>/.

Die Anbindung verwendet codex exec mit JSON-Ereignissen und einem Ausgabeschema. API-Key-Anmeldung wird abgewiesen; API-Key-Umgebungsvariablen werden nicht an den Kindprozess übergeben. Bei ausgeschöpftem Kontingent bleibt der Lauf mit waiting_for_quota gespeichert. [Nichtinteraktive Codex-Aufrufe](https://learn.chatgpt.com/docs/non-interactive-mode)

## 3. Lokale Sprachausgabe vorbereiten

FFmpeg und ffprobe müssen im PATH verfügbar sein. Beide werden für die Montage verwendet.

Für Qwen wird eine zweite Umgebung angelegt:

~~~powershell
py -3.12 -m venv .venv-tts
~~~

In dieser Umgebung zuerst die zu Windows-Version und Radeon passende PyTorch-/AMD-Laufzeit nach der aktuellen AMD-Anleitung installieren. Verwende dabei .\.venv-tts\Scripts\python.exe als Python. Die Treiber- und Wheel-Versionen sind noch nicht für deinen Rechner gemessen und werden deshalb nicht als bereits bestätigte Kombination vorgegeben. [AMD-Kompatibilitätsmatrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html)

Anschließend:

~~~powershell
.\.venv-tts\Scripts\python.exe -m pip install qwen-tts
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

Lokal geprüft sind Dateiverwaltung, Schemas, Prozessaufrufe, Kontingentzustände, Wiederaufnahme, Audio-Montage, Laufzeiten und Kapitel. Ein erfolgreicher echter Codex-Abo-Aufruf sowie Qwen auf deiner RX 9070 XT sind noch ausstehende Tests. Für Windows und Linux ist ein CI-Testlauf ohne Modellkonten eingerichtet.

Noch nicht implementiert sind Themenrecherche, Quellen- und Wissensmodell, ausführliche Skripterstellung, Serienplanung, fachliche Qualitätsgates, automatische Textreparatur und Aufteilung zu langer Folgen. run, research, ingest, model, plan, script, check, render und export aus der vollständigen Produktspezifikation werden deshalb noch nicht als fertige CLI-Funktionen angeboten.
