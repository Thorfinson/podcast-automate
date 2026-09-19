# macOS und Linux

Das lokale Browser-Studio verwendet auf allen Plattformen denselben Ablauf und dieselben Projektdateien. Python, FFmpeg und Codex werden auf jedem Rechner installiert. Für Gemini-Audio reicht der OpenRouter-Key im geschützten Key-Eingang; eine lokale Qwen-Installation ist dafür nicht nötig.

## Installation und Start

Der Controller benötigt Python **3.12 oder neuer**, außerdem `ffmpeg` und `ffprobe` im PATH. Die folgenden Befehle im Projektordner ausführen.

Auf macOS mit Homebrew:

```sh
brew install python@3.12 ffmpeg
sh scripts/setup.sh
sh Podcast-Studio.sh
```

Danach lässt sich **Podcast-Studio.command** im Finder doppelklicken. Das Setup setzt die Ausführungsrechte. Der Starter berücksichtigt auch die Homebrew-Pfade von Apple-Silicon- und Intel-Macs.

Auf Ubuntu 24.04 oder einer vergleichbaren Distribution mit Python ab 3.12:

```sh
sudo apt update
sudo apt install python3 python3-venv ffmpeg
sh scripts/setup.sh
sh Podcast-Studio.sh
```

Bei anderen Distributionen Python, das venv-Modul und FFmpeg über den jeweiligen Paketmanager installieren. Ein bestimmtes Python lässt sich beispielsweise mit `PYTHON=python3.13 sh scripts/setup.sh` auswählen. Das Setup verändert keine vorhandene fremde virtuelle Umgebung.

Das Studio öffnet `http://127.0.0.1:8765`; das Terminal bleibt während der Arbeit geöffnet. Für einen anderen Port: `sh Podcast-Studio.sh --port 8766`. Mit `--no-browser` startet nur der Server; die Adresse lässt sich dann im Browser desselben Rechners öffnen. Der Server bleibt ausschließlich an localhost gebunden.

Codex CLI muss im PATH verfügbar und mit dem vorhandenen Konto angemeldet sein. `runtime.codex_executable` erlaubt einen expliziten Programmpfad. Der Studio-Starter berücksichtigt zusätzlich `~/.local/bin`, `/opt/homebrew/bin` und `/usr/local/bin`. Claude Code (`claude`, mit `claude auth login` über claude.ai angemeldet) wird auf dieselbe Weise gefunden; es genügt, wenn eines der beiden Abos nutzbar ist.

Eine Prüfung ohne lokalen Qwen-Worker:

```sh
.venv/bin/pla doctor --skip-tts
```

Diese Prüfung kontrolliert Programme, beide Abo-Anmeldungen und den Kontingentstand (`pla quota` zeigt ihn allein); sie erzeugt kein Audio und prüft keinen OpenRouter-Key. Recherche läuft über das gewählte Abo; die Auswahl des Audioanbieters ist unabhängig davon.

## Optional: lokales Qwen

Qwen bekommt eine eigene **Python-3.12-Umgebung** in `.venv-tts`. Das Setup installiert feste Paketversionen, lädt die festgehaltene Modellrevision und prüft eine kleine Rechnung auf dem gewählten Gerät. Es erzeugt keine Hörprobe. Die getrennte Umgebung entspricht der [Qwen-Einrichtungsempfehlung](https://github.com/QwenLM/Qwen3-TTS#environment-setup).

Auf einem Apple-Silicon-Mac:

```sh
brew install sox
.venv/bin/python scripts/setup-qwen.py --device mps
```

MPS verwendet im Worker Float32 und keine CUDA-Aufrufe. Dieser Weg ist implementiert, benötigt aber noch einen echten Qwen-Hörtest auf Apple-Hardware. Bei Problemen lässt sich ausdrücklich `--device cpu` wählen; CPU-Vertonung kann deutlich länger dauern. Für Intel-Macs ist Gemini der vorgesehene Audioweg; das hier festgelegte Qwen/PyTorch-Paketset ist dafür nicht validiert.

Linux ohne GPU:

```sh
sudo apt install sox libsndfile1
.venv/bin/python scripts/setup-qwen.py --device cpu --torch-index-url https://download.pytorch.org/whl/cpu
```

Linux mit passender NVIDIA-GPU und einem Treiber für CUDA 12.8:

```sh
.venv/bin/python scripts/setup-qwen.py --device cuda:0 --torch-index-url https://download.pytorch.org/whl/cu128
```

Linux mit einer von ROCm 6.4 unterstützten AMD-GPU:

```sh
.venv/bin/python scripts/setup-qwen.py --device cuda:0 --torch-index-url https://download.pytorch.org/whl/rocm6.4
```

Der Wheel-Index muss zur Hardware und zum Treiber passen. Diese Beispiele verwenden PyTorch und torchaudio 2.9.1 gemäß der [offiziellen Versionsübersicht](https://pytorch.org/get-started/previous-versions/#v291). Die bestehende Windows/Radeon-Einrichtung bleibt in [qwen-windows.md](qwen-windows.md) beschrieben.

`--device auto` wählt CUDA/ROCm, sonst MPS, sonst CPU. Bei einer ausdrücklich gewählten GPU bricht das Setup ab, wenn sie fehlt. Es wechselt dann nicht unbemerkt zur CPU.

Die Einstellung wird lokal in `.studio/tts-runtime.json` gespeichert und für neue Studio-Projekte übernommen. Für ein vorhandenes Projekt:

```sh
.venv/bin/python scripts/setup-qwen.py --device mps --project projects/mein-projekt
.venv/bin/pla doctor projects/mein-projekt
```

Dabei wird die bisherige Projektkonfiguration vor der Änderung gesichert. Ein laufendes Projekt bleibt gesperrt. Die tatsächliche Spracherzeugung beginnt anschließend erst nach der Freigabe im Studio.

## Wechsel auf einen anderen Rechner

Repository und gewünschte Ordner unter `projects/` übertragen. `.venv`, `.venv-tts` und Windows-Binärprogramme nicht kopieren; sie werden lokal neu eingerichtet. Die persönlichen Projekte sind von Git ausgeschlossen und werden durch einen Git-Checkout allein nicht übertragen.

Fertige MP3-Dateien und Skripte bleiben lesbar. Vor einer neuen lokalen Vertonung das gewünschte Projekt mit `setup-qwen.py --project …` für diesen Rechner konfigurieren. Alte laufende Aufträge enthalten teilweise absolute Pfade und an ihre Eingaben gebundene Freigaben; ihre Wiederaufnahme auf einem anderen Rechner ist nicht zugesichert. Nach einer Konfigurationsänderung einen neuen Audiolauf freigeben. Lokal importierte Quellen und ausdrücklich gesetzte Programmpfade gegebenenfalls anpassen.

## Prüfstand

Die CI führt Python-, Browserlogik- und FFmpeg-Tests auf Windows, Ubuntu und macOS aus. Modellantworten werden dort simuliert; GPU-Modelle und API-Zugangsdaten sind nicht erforderlich. Hinzu kommen Tests für Unix-Starter, Plattformpfade, Geräteauswahl, Prozessketten und Papierkorb/Wiederherstellung. Das Einrichten der CI ersetzt keinen bereits durchgeführten Testlauf: Die macOS-/Linux-Jobs laufen beim nächsten Push oder Pull Request. Echte Qwen-Hörtests auf macOS und Linux stehen noch aus.
