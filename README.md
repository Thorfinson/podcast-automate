# Podcast Automate

Ein persönliches Recherche-zu-Podcast-System: Du gibst ein Thema vor, das Studio recherchiert belegt im Web, plant eine Deep-Dive-Serie, schreibt und prüft Dialogskripte und vertont freigegebene Folgen als MP3. Alles läuft lokal; Modellaufrufe gehen über dein Claude- oder Codex-Abo oder über OpenRouter, Sprache über lokales Qwen oder Gemini.

Die Erklärungen setzen kein Fach- oder Mathematikwissen voraus und bauen ihre Tiefe über vertraute Bilder, durchgearbeitete Beispiele und benannte Grenzen auf. Serienlänge und Folgenzahl ergeben sich aus dem Thema; eine Folge dauert bis zu 60 Minuten und wird in Teilen von höchstens 30 Minuten vertont.

## Schnellstart

| System | Einrichtung | Start |
| --- | --- | --- |
| Windows 11 | [Windows-Anleitung](docs/windows-quickstart.md) | `Podcast-Studio.cmd` doppelklicken |
| macOS | `brew install python@3.12 ffmpeg`, dann `sh scripts/setup.sh` ([Details](docs/macos-linux.md)) | `Podcast-Studio.command` doppelklicken |
| Linux | Python ab 3.12 und FFmpeg installieren, dann `sh scripts/setup.sh` | `sh Podcast-Studio.sh` |

Das Studio öffnet `http://127.0.0.1:8765` und ist nur auf diesem Rechner erreichbar. Für die belegte Web-Recherche wird ein angemeldetes Abo benötigt: Claude Code mit Claude-Max-Abo oder die Codex CLI mit ChatGPT-Abo; über OpenRouter wird nicht gesucht. Ein OpenRouter-Key ist optional und wird nur für die Sitzung im Speicher gehalten. `pla doctor --skip-tts` prüft die Installation ohne lokales Qwen.

## Ablauf im Studio

1. **Auftrag & Stimmen**: Thema, Tiefe, Sprache, Textmodell, Audioanbieter und Stimmen im Gespräch festlegen; eigene MD-, TXT- oder DOCX-Dateien anhängen.
2. **Recherche**: Live-Suche, Abruf der Originalquellen, feste Teilfragen mit Abschlusskriterien, unabhängige Antwortprüfung und Gesamtprüfung bis zum belegten Dossier.
3. **Inhaltsverzeichnis**: quellengebundener Serienplan, den du liest und ausdrücklich freigibst.
4. **Ausarbeitung**: je Folge Lehrkonzept, Dialogentwurf, Dialog-Polishing und vier getrennte Prüfungen; fehlende Grundlagen werden automatisch nachrecherchiert.
5. **Skripte lesen**: jede Folge als Text prüfen, bevor Audio entsteht.
6. **Vertonung**: erst nach deiner Freigabe des gelesenen Skripts; MP3 mit Kapiteln, Transkript und Show Notes.

Jeder Schritt ist wiederaufnehmbar. Fertige Ergebnisse werden per Hash gebunden; geänderte Eingaben erzwingen einen neuen Lauf statt stiller Neuberechnung. Details: [Studio](docs/studio.md), [Recherche](docs/research.md), [Skripte](docs/scripts.md), [Lehrplanung](docs/teaching-design.md), [Gemini-Audio](docs/gemini-audio.md).

## Anbieter und Kosten

- **Text**: Claude-Max-Abo über Claude Code (Standard seit 29.09.2026: Claude Sonnet 5.5 mit Effort `high`, ab Claude Code 2.1.284; Opus 5.5 ab 2.1.280 wählbar), Codex-Abo (GPT-6 Astra, Reasoning `xhigh`) oder OpenRouter-Modelle für Inhaltsverzeichnis, Lehrkonzept, Skripte, Polishing und Prüfungen. Die Vorauswahl **Automatisch** nimmt Claude, bis dessen Kontingent erschöpft ist, dann Codex, und pausiert erst, wenn beide Abos leer sind; ist ein Abo gerade nicht nutzbar (Anmeldung abgelaufen, kein Abo, CLI fehlt oder zu alt), nimmt sie seit 02.10.2026 für zehn Minuten (`subscriptions.UNAVAILABLE_SECONDS`) das andere, statt anzuhalten. Die Web-Recherche läuft über das gewählte Abo. Die Kataloge stehen in `text_settings.py`; `pla doctor` nennt ihr Prüfdatum, `pla quota` den Kontingentstand beider Abos. [Plan und Umsetzungsstand](docs/claude-backend-plan.md).
- **Audio**: lokales Qwen3-TTS (Windows mit AMD-GPU erprobt) oder Gemini 3.8 Flash TTS (`google/gemini-3.8-flash-tts`) über OpenRouter mit 30 Stimmen und gemeinsamer Hörprobenbibliothek.
- **Budget**: Ein Recherche- oder Skriptlauf hat standardmäßig 750 Modellaufrufe (`ResearchLimits.model_calls`). Ein Recherchelauf hält nach der Planung an und legt eine Hochrechnung vor (Teilfragen, voraussichtliche Aufrufe, Stunden; Erfahrungswert 16 Aufrufe je Teilfrage, `question_budget.DEFAULT_CALLS_PER_TASK`, gemessen ab dem ersten veröffentlichten Lauf); erst die Freigabe des Rechercheplans im Studio oder mit `pla approve --research-plan` startet die Teilfragen, `pla research --approve-plan` verzichtet auf den Stopp. Ein Skriptlauf braucht ohne Reparaturen mindestens einen Aufruf für das Inhaltsverzeichnis und neun je Folge (`script_budget.STAGE_CALLS`); die Prognose steht vor jeder kostenpflichtigen Stufe im Studio, neben der Untergrenze auch die Erwartung nach dem letzten abgeschlossenen Skriptlauf des Projekts. Nur die Untergrenze entscheidet: Ein zu knappes Limit stoppt den Lauf, bevor Aufrufe verbraucht werden. [Planfreigabe](docs/research.md#planfreigabe-und-hochrechnung), [Aufrufe je Folge](docs/scripts.md#modellaufrufe-je-folge).

## Einzelbefehle

| Befehl | Zweck |
| --- | --- |
| `pla studio` | Studio im Browser öffnen; `--lan` (unter Windows `Podcast-Studio-WLAN.cmd`) auch vom Handy im Heimnetz |
| `pla init <projekt> --topic "…"` | Projekt mit validiertem Auftrag anlegen; trägt die auf dem Rechner bereits genutzte Qwen-Revision ein |
| `pla doctor [<projekt>] [--skip-tts]` | Installation, Codex- und Claude-Anmeldung, Abo-Kontingent, TTS-Umgebung und Katalogalter prüfen |
| `pla quota` | Kontingent beider Abos ohne Modellaufruf anzeigen |
| `pla research <projekt>` | Belegtes Dossier erstellen |
| `pla script <projekt> [--episode ep_001] [--backend auto\|claude_code\|openrouter …]` | Serienplan, Lehrkonzepte und geprüfte Skripte |
| `pla audio <projekt> --episode ep_001 --approve-audio` | Freigegebenes Skript vertonen |
| `pla status <projekt>` / `pla resume <projekt>` | Fortschritt, Fehlerprotokolle, Wiederaufnahme |
| `pla text-probe [--backend claude_code]` / `pla audio-probe --approve-audio` | Technische Proben ohne Recherche |
| `pla schemas <ordner>` | Alle 21 Datenverträge als JSON-Schema exportieren |

Unter Windows: `.\.venv\Scripts\pla.exe …`, unter macOS/Linux: `.venv/bin/pla …`.

## Projektstruktur

```
projects/<projekt>/
  project.yaml            Auftrag und Laufzeiteinstellungen (keine Zugangsdaten)
  studio/                 gewählter Textanbieter, Audioanbieter, Ausführungsmodus, Chat, Auftragsstatus
  research/, models/      Dossier, Quellenindex, Serienplan, Wissensmodell
  episodes/<ep>/          script.yaml, script.md, Lehrplan, Show Notes, Audio-Freigabe
  runs/<run_id>/          Manifest, Zwischenstände, Modellaufrufe (je Aufruf provider_choice.json), budget_projection.json, failures/
  exports/<ep>/<run_id>/  MP3, Kapitel, Transkript
src/podcast_automate/
  prompts/                alle Modellanweisungen als Textdateien (siehe prompts/README.md)
  research.py, question_*.py            Recherche: Ablauf, Teilfragen, Synthese
  scripting.py, script_pipeline.py      Skriptlauf: Vorbereitung und Stufen
  studio.py, studio_worker.py           lokaler Server und Auftrags-Prozesse
```

Protokolle: `.studio/studio.log`, `<projekt>/studio/worker.log`, `<projekt>/logs/pla.log`; bereinigte Tracebacks fehlgeschlagener Stufen unter `runs/<run_id>/failures/`.

## Qualität und Grenzen

Jede Modellantwort muss ein striktes JSON-Schema erfüllen und wird deterministisch geprüft: wörtliche Zitate, existierende Quellenabschnitte, Zitatgrenzen je Quelle, Abhängigkeitsreihenfolge und vollständige Kriterienabdeckung. Bestandene Prüfungen müssen Textstellen belegen. Was das System nicht leistet: menschliche Fach- und Hörabnahme, öffentliche Rechteklärung einzelner Quellen und eine allgemeine Anonymisierung. [SPEC.md](SPEC.md) beschreibt Zielumfang und Abnahmekriterien, das [Umsetzungsprotokoll des Qualitätsaudits vom 19. September 2026](docs/quality-audit-2026-09-19-implementation.md) die durchgeführten Verifikationen.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v   # FFmpeg im PATH, einige Minuten
node --test tests/studio_ui.test.cjs
```

Modellantworten werden simuliert; FFmpeg wird echt aufgerufen. Umfang der Suiten und welche Tests zu welchem Modul gehören, steht in [AGENTS.md](AGENTS.md). Regressionen mit echten Modellaufrufen liegen unter [evals/](evals/).

## Dokumentation

Der Index [docs/README.md](docs/README.md) ordnet alle Anleitungen ein.

- Einstieg und Einrichtung: [docs/windows-quickstart.md](docs/windows-quickstart.md), [docs/macos-linux.md](docs/macos-linux.md), [docs/qwen-windows.md](docs/qwen-windows.md), [docs/gemini-audio.md](docs/gemini-audio.md)
- Referenz zum heutigen Verhalten: [docs/studio.md](docs/studio.md), [docs/research.md](docs/research.md), [docs/research-evidence.md](docs/research-evidence.md), [docs/scripts.md](docs/scripts.md), [docs/teaching-design.md](docs/teaching-design.md), [docs/system-quality-assessment.md](docs/system-quality-assessment.md)
- Pläne mit Umsetzungsstand: [docs/claude-backend-plan.md](docs/claude-backend-plan.md), [docs/quality-audit-2026-09-19-implementation.md](docs/quality-audit-2026-09-19-implementation.md)
- Archiv: [docs/history/quality-audit-2026-09-19.md](docs/history/quality-audit-2026-09-19.md) und [docs/history/quality-audit-2026-09-19-plan.md](docs/history/quality-audit-2026-09-19-plan.md); [SPEC.md](SPEC.md) beschreibt Zielumfang und Abnahmekriterien, [AGENTS.md](AGENTS.md) die Testregeln

## Lizenz

MIT, siehe [LICENSE](LICENSE).
