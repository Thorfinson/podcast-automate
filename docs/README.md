# Dokumentation

[SPEC.md](../SPEC.md) im Repository-Wurzelverzeichnis beschreibt Zielumfang und Abnahmekriterien; [README.md](../README.md) beschreibt den aktuellen Stand.
[AGENTS.md](../AGENTS.md) enthält die Testregeln: welche Suite wann läuft und was ein Test nicht darf.

## Einstieg und Einrichtung

- [windows-quickstart.md](windows-quickstart.md): Wie installiere ich die Anwendung unter Windows 11, prüfe Codex- und Claude-Abo, bereite die lokale Sprachausgabe vor und erzeuge die erste Hörprobe?
- [macos-linux.md](macos-linux.md): Wie installiere und starte ich das Studio unter macOS oder Linux, optional mit lokalem Qwen, und was ist beim Wechsel auf einen anderen Rechner zu beachten?
- [qwen-windows.md](qwen-windows.md): Wie richte ich Qwen3-TTS in `.venv-tts` mit AMD-PyTorch und festen Versionen ein und vergleiche die neun eingebauten Stimmen auf Deutsch und Englisch?
- [gemini-audio.md](gemini-audio.md): Wie wähle ich Gemini 3.1 Flash TTS über OpenRouter mit seinen 30 Stimmen und der gemeinsamen Hörprobenbibliothek, und was ist daran noch nicht praktisch geprüft?

## Referenz: so verhält sich das Produkt heute

- [studio.md](studio.md): Was zeigt das Browser-Studio in jedem Schritt, welche Freigaben verlangt es, wie hält es Aufträge an und setzt sie fort, und wo liegen die Protokolle zur Fehlerdiagnose?
- [research.md](research.md): Wie führt `pla research` vom Thema über Live-Suche und abgerufene Quellen zum belegten Dossier, welche Limits gelten, und wie wird ein Lauf fortgesetzt?
- [research-evidence.md](research-evidence.md) (englisch): Welche Belegverträge prüfen jeden Befund, Quellenrollen, Unabhängigkeit und Synthesevergleiche, welche Artefakte entstehen dabei, und was belegen sie nicht?
- [scripts.md](scripts.md): Wie erzeugt `pla script` Serienplan, Dialogskripte, Polishing und Prüfungen, wie wähle ich Anbieter und Modell, wie überarbeite ich einen Text, und wie viele Modellaufrufe kostet eine Folge?
- [teaching-design.md](teaching-design.md): Welche Lehrplanung muss jeder neue Skriptlauf vor dem Schreiben vorlegen, und welche drei redaktionellen Prüfungen muss der Dialog danach bestehen?
- [system-quality-assessment.md](system-quality-assessment.md): Nach welchen Kriterien werden Recherche, Erklärungstiefe, Serienaufbau und Hörqualität bewertet, und wie läuft die Abnahme?

## Pläne mit Umsetzungsstand

- [claude-backend-plan.md](claude-backend-plan.md): Wie wird Claude Code zum zweiten Abo-Anbieter neben Codex mit Kontingentprüfung vor jedem Aufruf, und welche Phasen davon sind umgesetzt?
- [quality-audit-2026-09-19-implementation.md](quality-audit-2026-09-19-implementation.md) (englisch): Welche Arbeitspakete des Qualitätsaudits vom 19. September 2026 sind umgesetzt, wo weicht die Umsetzung vom Plan ab, wie steht die Beispielserie gemessen da, und was fehlt noch?

## Archiv (history/)

- [history/quality-audit-2026-09-19.md](history/quality-audit-2026-09-19.md) (englisch): Der Prüfbericht vom 19. September 2026 anhand der Transformer-Beispielserie mit den Vorschlägen P-01 bis P-16.
- [history/quality-audit-2026-09-19-plan.md](history/quality-audit-2026-09-19-plan.md) (englisch): Der zugehörige Umsetzungsplan mit sechzehn Arbeitspaketen in drei Phasen.

Überholte Protokolle wurden am 19. September 2026 entfernt und bleiben in der Git-Historie (`git log -- docs/<datei>`): `entwicklungsnotizen.md`, `personal-learning-podcast-system-plan.md`, `quality-verification.md`, `windows-pilot.md`, `research-skills-comparison.md` und `research-analysis.md`.
