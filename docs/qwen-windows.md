# Qwen3-TTS auf Windows mit Radeon

Die Podcast-Anwendung verwendet einen eigenen Qwen-Prozess in `.venv-tts`. Die am 13.09.2026 geprüfte Lemonade-Installation 10.6.0 bietet Kokoro als TTS-Backend, aber keinen Qwen3-TTS-Eintrag in ihrem Modellkatalog oder ihren Recipes. Ein laufender Lemonade-Server stellt daher diese Qwen-Umgebung nicht bereit. Die Einrichtung unten verwendet den bestehenden lokalen Worker des Projekts.

## Einrichtung

Voraussetzungen sind der installierte Controller in `.venv`, ein mit `pla init` angelegtes Projekt und ein für AMD-PyTorch geeigneter Radeon-Treiber. Der Zielrechner hat Windows 11 Pro, eine RX 9070 XT und Treiber 32.0.31041.1004 vom 17.08.2026. [AMD-Installationsanleitung und Treibervoraussetzungen](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html).

Im Repository-Verzeichnis:

```powershell
powershell -NoProfile -File .\scripts\setup-qwen.ps1 -ProjectDir .\projects\windows-pilot
```

Das Setup:

- installiert bei Bedarf Python 3.12.14 mit `uv` unter `tools/python/` und legt `.venv-tts` an,
- installiert die AMD-Wheels und Qwen aus `requirements-tts-windows.txt`,
- prüft Paketabhängigkeiten, den Qwen-Import und eine BF16-Berechnung auf der GPU,
- lädt `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` samt Sprach-Tokenizer in den normalen Hugging-Face-Cache unter `%USERPROFILE%/.cache/huggingface/hub/`,
- setzt TTS-Python, Modell, feste Modellrevision, `cuda:0` und `eager` in der gewählten `project.yaml`,
- bewahrt den ursprünglichen Projektauftrag als `reports/project-before-qwen.yaml` auf und schreibt Umgebungsbericht und installierte Paketversionen nach `reports/`.

Die Downloads umfassen mehrere Gigabyte. Binärdateien, virtuelle Umgebungen und Modellgewichte gehören nicht ins Git-Repository. Das Setup startet keine Audioerzeugung. Bereits vorhandene Stimmen und andere Themeneinstellungen bleiben erhalten. Geänderte Konfiguration erfordert einen neuen Probenlauf; alte Textläufe und ihre Artefakte bleiben gespeichert.

## Festgelegte Versionen

| Bestandteil | Version / Einstellung |
| --- | --- |
| TTS-Python | 3.12.14 |
| PyTorch / Torchaudio | 2.9.1+rocm7.2.1 |
| ROCm SDK | 7.2.1 |
| Qwen-Paket | qwen-tts 0.1.1 |
| Transformers | 4.57.3 |
| Modell | Qwen3-TTS-12Hz-0.6B-CustomVoice |
| Modellrevision | `85e237c12c027371202489a0ec509ded67b5e4b5` |
| Stimmen im Pilot | Ryan und Serena, Sprache German |
| Gerät / Attention | `cuda:0` / `eager` |

Das Modell und seine Aufrufe folgen der [Qwen-Dokumentation](https://github.com/QwenLM/Qwen3-TTS). Es wird keine FlashAttention-Installation vorausgesetzt. Die `cuda`-Gerätebezeichnung ist bei diesem AMD-PyTorch-Build die verwendete Schnittstelle.

## Hörprobe und Wiederaufnahme

FFmpeg muss wie in der [Windows-Anleitung](windows-quickstart.md) eingerichtet sein. In jedem neuen PowerShell-Fenster:

```powershell
$ffmpegBin = (Resolve-Path .\tools\ffmpeg\bin).Path
$env:PATH = "$ffmpegBin;$env:PATH"
.\.venv\Scripts\pla.exe doctor .\projects\windows-pilot --json
.\.venv\Scripts\pla.exe audio-probe .\projects\windows-pilot --approve-audio
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
```

Nach einer Unterbrechung desselben unveränderten Audiolaufs:

```powershell
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot
```

Die fertige Hörprobe liegt unter `projects/windows-pilot/probes/audio/<run_id>/audio.mp3`, begleitet von Transkript, Kapiteln, Timeline und Messbericht. Aussprache, Natürlichkeit und Stimmenkonstanz werden anhand der MP3 beurteilt. Die technischen Berichte behaupten keine bereits bestandene Hörprüfung.

## Deutsch und Englisch vergleichen

`language` in `project.yaml` wählt für `audio-probe` sowohl den mitgelieferten Dialog als auch die Qwen-Sprache: `de-DE` für Deutsch (Standard), `en-US` für Englisch. Transkript und Kapiteltitel folgen der gewählten Sprache. Die Sprachwahl fließt in den Audio-Cache ein.

Die aktuell gewählten Stimmen für die englische Variante:

```yaml
language: en-US
voice_profile:
  host_a: Aiden
  host_b: Vivian
```

Für den direkten Vergleich gibt es lokal zwei Projekte: `projects/windows-pilot/` auf Deutsch und `projects/windows-pilot-en/` mit der englischen Übersetzung. Beide verwenden aktuell Aiden als `host_a` und Vivian als `host_b`, dieselbe Modellrevision, denselben Seed und dieselben Pausen. Die bereits erzeugten ursprünglichen Dialogproben wurden mit Ryan und Serena gesprochen. Die Texte behandeln dieselben Inhalte; durch die Sprache können sich Sprechtempo und Dauer unterscheiden.

```powershell
.\.venv\Scripts\pla.exe audio-probe .\projects\windows-pilot-en --approve-audio
```

Eine Änderung der Sprache oder Stimmen in einem bestehenden Projekt erfordert einen neuen `audio-probe`-Lauf. `resume` setzt nur einen Lauf mit unveränderten Eingaben fort. Diese Sprachwahl betrifft die technische Hörprobe; die Codex-Verbindungsprobe bleibt unverändert.

## Alle neun Stimmen anhören

Der installierte CustomVoice-Modellstand enthält `Ryan`, `Serena`, `Aiden`, `Vivian`, `Uncle_Fu`, `Ono_Anna`, `Sohee`, `Eric` und `Dylan`. Ein separates Skript erzeugt für jede Stimme denselben kurzen Text auf Englisch und Deutsch. Es übernimmt die Qwen-Einstellungen des angegebenen Projekts und verändert dessen Stimmenkonfiguration nicht.

Mit FFmpeg im aktuellen PATH:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate-voice-samples.py --project-dir .\projects\windows-pilot
```

Die Ergebnisse liegen unter `projects/voice-samples/`: [Hörindex mit Einzeldateien und Zeitmarken](../projects/voice-samples/README.md), 18 einzelne MP3-Dateien sowie eine durchgehende Vergleichsdatei pro Sprache. Alle Dateien sind lokal und von Git ausgeschlossen. Die Reihenfolge der durchgehenden Dateien entspricht der Stimmenliste oben; Kapiteldateien und Transkripte ordnen die Abschnitte den Stimmen zu.

`--languages en-US` oder `--languages de-DE` begrenzt einen neuen Aufruf auf eine Sprache; `--output-dir` wählt einen anderen Ausgabeordner. Bei erneutem Ausführen mit denselben Einstellungen werden bereits gültige Qwen-Segmente aus dem Cache wiederverwendet. Die Montage wird erneut ausgeführt. Zum Auswählen einer Stimme ihren Namen aus der Liste in `voice_profile.host_a` oder `voice_profile.host_b` übernehmen und einen neuen Probenlauf starten; die beiden Hosts benötigen unterschiedliche Stimmen.
