# Erster Windows-Versuch: 13.09.2026

Der Versuch bestätigt Projektverwaltung, die echte Codex-Abo-Anbindung, automatische Audio-Montage mit Testsignalen und anschließend auch die vollständige lokale Qwen-Spracherzeugung auf dem Zielrechner. Die redaktionelle Hörprüfung ist noch offen.

## Umgebung

| Komponente | Gemessener Stand |
| --- | --- |
| Betriebssystem | Windows 11 Pro, 64 Bit, Build 10.0.26200 |
| Grafikkarte | AMD Radeon RX 9070 XT |
| Radeon-Treiber | 32.0.31041.1004 |
| Controller-Python | 3.13.14, projektlokale `.venv` |
| Codex CLI | 0.154.0-alpha.6.2, ChatGPT-Anmeldung |
| FFmpeg / ffprobe | 9.0.1 Essentials, projektlokal unter `tools/ffmpeg/bin/` |

Die Anwendung erlaubt Python ab 3.12; der Controller-Test lief mit dem bereits verfügbaren Python 3.13.14. Im anschließenden Qwen-Versuch wurde eine separate Python-3.12.14-Umgebung mit AMD-PyTorch eingerichtet; Ergebnisse siehe unten.

## Durchgeführte Prüfungen

- FFmpeg-Archiv von gyan.dev geladen und gegen die im Setup festgehaltene SHA-256-Prüfsumme geprüft.
- Paket in `.venv` installiert; alle 24 Tests bestanden, keine übersprungen. Die Audio-Tests erzeugen echte MP3-Dateien und prüfen unter anderem Format, Lautheit, Dauer, Kapitel, fehlerhafte Segmente und Wiederaufnahme nach Montagefehlern.
- Projekt `projects/windows-pilot/` mit dem Thema „Energiebasierte Modelle verstehen“ angelegt.
- `text-probe` erfolgreich über die bestehende ChatGPT-Anmeldung ausgeführt. Die Antwort enthält validierte Vertiefungsfragen und kennzeichnet ausdrücklich, dass keine Recherche stattfand.
- `resume` auf demselben Lauf ausgeführt: gespeicherte Ergebnisse wiederverwendet, Stufenversuche weiterhin `1`.
- `doctor` bestätigt Python, FFmpeg, ffprobe und Codex-Anmeldung. Der TTS-Check schlägt fehl: In der aktuell konfigurierten Controller-Umgebung fehlen PyTorch, Qwen, SoundFile und Transformers.

Lokale Nachweise liegen im von Git ausgeschlossenen Projektordner: `runs/run_20260913_084431_132714_cdf05955/`, die zugehörigen Ergebnisse unter `probes/text/` sowie `reports/doctor.json` und `reports/tts_environment.json`. Sie werden nicht mit dem Repository verteilt.

## Anschließender Qwen-Versuch

Lemonade 10.6.0 wurde lokal geprüft. Sein Modellkatalog und seine Recipes enthalten Kokoro für TTS, jedoch keinen Qwen-TTS-Eintrag. Der Versuch nutzt den bestehenden separaten Qwen-Worker des Projekts. [Reproduzierbare Einrichtung](qwen-windows.md).

Python 3.12.14, PyTorch/Torchaudio 2.9.1+rocm7.2.1, Qwen 0.1.1 und Transformers 4.57.3 wurden installiert. Die GPU wurde als RX 9070 XT erkannt; FP32-, FP16- und BF16-Matrixberechnungen bestanden. Anschließend meldete `doctor` alle Voraussetzungen als verfügbar.

`Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` wurde mit Revision `85e237c12c027371202489a0ec509ded67b5e4b5` geladen. Die vier deutschen Segmente mit Ryan und Serena wurden vollständig erzeugt und automatisch montiert:

| Messung | Ergebnis |
| --- | --- |
| MP3-Dauer einschließlich Pausen | 58,79 Sekunden |
| Reine Sprachausgabe | 56,64 Sekunden |
| Modellladezeit | 2,35 Sekunden |
| Synthesezeit der vier Segmente | 335,33 Sekunden |
| Gesamtlauf einschließlich Montage | etwa 5 Minuten 45 Sekunden |
| Spitzenwert belegter GPU-Speicher laut PyTorch | 2,97 GiB |
| MP3-Format | 44,1 kHz, Stereo, 192 kbit/s |
| Gemessene Lautheit der MP3 | -16,53 LUFS, Ziel -16 LUFS |
| Gemessener True Peak | -1,75 dBTP |
| Kapitel / Skriptsegmente | 2 / 4 |
| Wiederaufnahme | Alle Artefakthashes gültig, keine neue Synthese oder Montage; je Stufe weiterhin ein Versuch |

Die Synthese benötigt mit BF16 und `eager` etwa 5,9 Sekunden pro Sekunde gesprochener Ausgabe. Eine Leistungsoptimierung und ein Vergleich mit größeren Modellvarianten sind damit noch nicht erfolgt.

Die [lokale MP3](../projects/windows-pilot/probes/audio/run_20260913_085922_045562_cd921300/audio.mp3) liegt neben `chapters.json`, `transcript.md`, `timeline.json` und `audio_report.json`. Diese Dateien sind von Git ausgeschlossen und nur auf dem Zielrechner vorhanden. Lauf-ID: `run_20260913_085922_045562_cd921300`.

Zusätzliche Nachweise liegen unter `projects/windows-pilot/reports/`: `qwen_environment.json`, `doctor-qwen.json`, `tts-requirements-installed.txt` und `qwen-pilot-verification.json`. Die ursprüngliche Projektkonfiguration wurde vor der TTS-Umstellung gesichert; der frühere Textlauf bleibt erhalten.

## Englische Vergleichsprobe

Die englische Übersetzung desselben Dialogs wurde im separaten Projekt `projects/windows-pilot-en/` erzeugt. Gegenüber dem deutschen Projekt unterscheidet sich nur `language: en-US`: Stimmen Ryan und Serena, Modellrevision, Seed, Gerät, Attention und Pausenzeiten sind gleich. Qwen erhielt für alle vier Segmente ausdrücklich `English`; Transkript und Kapiteltitel sind ebenfalls englisch.

| Messung | Deutsch | Englisch |
| --- | --- | --- |
| MP3-Dauer einschließlich Pausen | 58,79 Sekunden | 72,39 Sekunden |
| Reine Sprachausgabe | 56,64 Sekunden | 70,24 Sekunden |
| Synthesezeit | 335,33 Sekunden | 410,55 Sekunden |
| Gesamtlauf einschließlich Montage | etwa 5 Minuten 45 Sekunden | etwa 7 Minuten |
| Spitzenwert belegter GPU-Speicher laut PyTorch | 2,97 GiB | 3,14 GiB |
| Gemessene Lautheit | -16,53 LUFS | -16,38 LUFS |
| Gemessener True Peak | -1,75 dBTP | -1,76 dBTP |

Direkter Hörvergleich: [Deutsch anhören](../projects/windows-pilot/probes/audio/run_20260913_085922_045562_cd921300/audio.mp3), [Englisch anhören](../projects/windows-pilot-en/probes/audio/run_20260913_091844_598833_715cd8b7/audio.mp3), [englisches Transkript](../projects/windows-pilot-en/probes/audio/run_20260913_091844_598833_715cd8b7/transcript.md). Die Dateien liegen lokal und sind von Git ausgeschlossen.

Beide Dateien sind MP3 mit 44,1 kHz, Stereo und 192 kbit/s. Der englische Lauf enthält zwei Kapitel und vier Segmente; Wiederaufnahme verwendet alle Artefakte ohne neue Synthese oder Montage. Auch die Wiederaufnahme des vorhandenen deutschen Laufs blieb nach der Spracherweiterung gültig. Alle 25 automatisierten Tests bestanden, einschließlich Sprachübergabe an den Worker und Wiederaufnahme einer englischen Montage. Messbericht: `projects/windows-pilot-en/reports/qwen-pilot-verification.json`. [Sprachwahl einstellen](qwen-windows.md#deutsch-und-englisch-vergleichen).

## Vergleich aller neun Stimmen

Alle neun eingebauten Stimmen des installierten CustomVoice-Modells wurden mit einem eigenen kurzen Testtext pro Sprache aufgenommen. Innerhalb einer Sprache liest jede Stimme exakt denselben Text mit demselben Seed und denselben Modelleinstellungen.

- [Hörindex: 18 Einzelproben mit Zeitmarken und Konfigurationsnamen](../projects/voice-samples/README.md)
- [Alle englischen Stimmen hintereinander](../projects/voice-samples/en-US/all-voices/audio.mp3): 115,62 Sekunden
- [Alle deutschen Stimmen hintereinander](../projects/voice-samples/de-DE/all-voices/audio.mp3): 110,10 Sekunden

Reihenfolge: Ryan, Serena, Aiden, Vivian, Uncle_Fu, Ono_Anna, Sohee, Eric, Dylan. Die Einzelproben dauern etwa 9,5 bis 16,1 Sekunden. Die durchgehenden Dateien enthalten zusätzliche Pausen; ihre Kapiteldateien und der Hörindex identifizieren die Stimmen über Zeitmarken.

Alle 20 MP3-Dateien wurden auf Format (44,1 kHz, Stereo, 192 kbit/s), Dateihashes, Dauer und True Peak geprüft. Die gemessene Lautheit der Einzelproben liegt auf Englisch zwischen -16,89 und -16,27 LUFS und auf Deutsch zwischen -16,75 und -16,26 LUFS; Ziel ist jeweils -16 LUFS. Es wurde kein True-Peak-Clipping gemessen. Bei erneutem Ausführen wurden alle 18 Qwen-Segmente aus dem Cache wiederverwendet. Die Nachweise liegen lokal unter `projects/voice-samples/verification.json`.

Das Skript `scripts/generate-voice-samples.py` erzeugt die Proben erneut aus der vorhandenen Qwen-Konfiguration. [Befehl und Stimmenauswahl](qwen-windows.md#alle-neun-stimmen-anhören). Die Audiodateien und zugehörigen Berichte sind von Git ausgeschlossen.

## Gewählte Stimmen für weitere Proben

Nach dem Stimmenvergleich wurde vorläufig **Aiden als `host_a`** und **Vivian als `host_b`** gewählt. Diese Reihenfolge ist in `projects/windows-pilot/project.yaml` und `projects/windows-pilot-en/project.yaml` eingestellt und gilt für neue Audioläufe. Die oben dokumentierten ursprünglichen Dialogproben wurden mit Ryan und Serena erzeugt.

## Echter Rechercheversuch

`pla research` wurde mit dem Thema „Energiebasierte Modelle verstehen“ ausgeführt. Lauf `run_20260913_102516_846900_de6f1a32` enthält den ursprünglichen Such- und Abrufnachweis:

| Prüfung | Ergebnis |
| --- | --- |
| Tatsächliche Web-Suchanfragen im CLI-Protokoll | 4, innerhalb eines Discovery-Aufrufs |
| Ausgewählte Quellen | 7 |
| Erfolgreich eingelesen | 6: vier PDFs und zwei HTML-Lehrtexte |
| Gespeicherte / für die Auswertung ausgewählte Textabschnitte | 280 / 207 |
| Quellenverweise und kurze Belegzitate | Gegen gespeicherte Abschnitte geprüft |
| Modellreview | Keine verbleibenden blockierenden Befunde |

Der Abruf von LeCuns „A Tutorial on Energy-Based Learning“ schlug fehl und bleibt als Zugriffsproblem dokumentiert. Die anderen Quellen stammen von Hinton, Hyvärinen, Du/Mordatch, Nijkamp und Mitautoren sowie aus den NYU-Lehrnotizen. Bei der PDF-Prüfung wurde die benötigte Font-Unterstützung ergänzt und der gespeicherte Quellenstand erneut verarbeitet. Ein vollständiges Zahlenbeispiel für einen Trainingsschritt blieb offen; die Quellenlage wurde nicht als lückenlos ausgewiesen.

Das [aktuelle Dossier](../projects/windows-pilot/research/research_briefing.md), der [Rechercheplan](../projects/windows-pilot/research/research_plan.yaml) und die [offenen Fragen](../projects/windows-pilot/research/open_questions.md) liegen lokal im ausgeschlossenen Projektordner. Der ursprüngliche Lauf behält sein eigenes Dossier und seinen Prüfbericht unter `runs/`; `reports/research-pilot-verification.json` hält den damaligen technischen Nachweis fest. Wiederaufnahme desselben unveränderten Auftrags benötigte keine neuen Modellaufrufe oder Downloads.

Auf Wunsch gilt für beide Sprachprojekte nun: keine fachlichen oder mathematischen Vorkenntnisse voraussetzen, Ideen über vertraute Situationen und klare mentale Bilder aufbauen, nötige Begriffe erst danach erklären. Metaphern erhalten ausdrücklich eine Grenze. Der neue Befehl `research --reuse-sources <run_id>` ermöglicht dafür einen neuen Schreib- und Prüflauf aus den bereits recherchierten Quellen.

Der anschließende Lauf `run_20260913_105423_258930_8f6b526e` übernimmt die sechs Quellen des ursprünglichen Suchlaufs und enthält **13 Befunde mit sechs erklärten Bildern samt Grenzen**. Eine Landschaft und ein eingezeichneter Weg veranschaulichen unter anderem Bewertung, Suche und Lernen. Formale Herleitungen bleiben als Vertiefung offen: Drei Forschungsfragen sind beantwortet, drei teilweise beantwortet. Mehrere Überarbeitungen korrigierten abstrakte Erklärungen und zu weit gehende Aussagen. Der abschließende Quellenreview meldet keine verbleibenden Einwände; eine menschliche Abnahme der späteren Folge steht weiterhin aus.

Für diesen Schreib- und Prüflauf wurden elf Modellaufrufe und keine neue Suchrunde verwendet. Quellenhashes und Belege sind gültig. Die anschließende Wiederaufnahme änderte weder das Dossier noch Stufenversuche und rief weder Modell noch Downloads auf. Nachweis: `projects/windows-pilot/reports/plain-language-dossier-verification.json`.

Die erweiterte Testsuite besteht aus **47 bestandenen Tests**, einschließlich echter FFmpeg-Montage, Quellenübernahme nach Stiländerung und Ablehnung veränderter oder beschädigter Quellen.

## Erstes recherchiertes Dialogskript

`pla script .\projects\windows-pilot --episode ep_001` hat das geprüfte Dossier zum kompakten Wissensmodell, einem vorläufigen Entwurf mit vier Folgen und dem vollständigen Skript der ersten Folge verarbeitet. Lauf: `run_20260913_112422_602643_152184a3`.

- [Erste Folge lesen: aktuelle Fassung](../projects/windows-pilot/episodes/ep_001/script.md)
- [Serienentwurf ansehen](../projects/windows-pilot/research/series_outline.md)
- [Quellen und Hinweise zur ersten Folge](../projects/windows-pilot/episodes/ep_001/show_notes.md)

Aiden und Vivian erklären Bewerten, Suchen und Lernen anhand eines ovalen Weges mit Messpunkten. Das Skript enthält 2.225 Wörter in 34 Sprechersegmenten und fünf Kapiteln. Die geschätzte Sprechzeit einschließlich geplanter Pausen beträgt 17,46 Minuten bei 130 Wörtern pro Minute beziehungsweise vorsichtig 22,59 Minuten bei 100. Es liegt noch keine gemessene Audiodauer vor.

Die erste Folge verwendet fünf Befunde aus drei eingelesenen Quellen. Plan, Skript und unabhängiger Modellreview benötigten drei Modellaufrufe und keine neue Websuche. Struktur- und Quellenzuordnung sind geprüft; der Modellreview meldet keine blockierenden Einwände. Die anderen drei Folgen sind bisher nur geplant. Die aktuelle Testsuite besteht **57 Tests**, einschließlich des Schutzes manuell bearbeiteter kanonischer Skripte und der Wiederaufnahme ohne erneutes Schreiben.

**Nutzerwunsch: zuerst das Skript lesen, danach über Audio entscheiden.** Der zugehörige Status steht in `projects/windows-pilot/episodes/audio_review.yaml`, gebunden an den Skripthash und mit `audio_approved: false`. Für diese Folge wurde kein Audio erzeugt. Das technische Prüfprotokoll liegt unter `projects/windows-pilot/reports/script-pilot-verification.json`.

## Straffere Fassung nach der Leseprüfung

Die Rückmeldung zur ersten Fassung lautete: zu viel Vereinfachung und zu ausführliches Erklären des Selbstverständlichen. Der Lauf `run_20260913_122418_576447_3c9cf9fe` überarbeitet deshalb dieselbe Folge mit dem bestehenden Plan und denselben Quellen. Begriffe werden knapp eingeführt und normal verwendet; Wiederholungen, belehrende Vorreden und mehrfache Metaphernwarnungen entfallen weitgehend. Das ausgearbeitete Beispiel bleibt erhalten. Die oben genannten 2.225 Wörter und Laufzeitschätzungen beziehen sich auf die erste Fassung.

Das aktuelle Skript steht weiterhin unter `episodes/ep_001/script.md`. Struktur- und Quellenprüfung sowie der erneute Modellreview sind bestanden. Die Standards beider Sprachprojekte wurden entsprechend angepasst; Audio bleibt unfreigegeben. Die aktuelle Testsuite besteht **59 Tests**. Vergleichszahlen, Skripthash und Wiederaufnahmeprüfung stehen in `projects/windows-pilot/reports/script-tone-revision-verification.json`.

## Fachliche Tiefe von Grund auf

Die gekürzte Fassung mit 1.326 Wörtern erfüllte den gewünschten Anspruch nicht. Die neue Vorgabe lautet: universitäres Verständnis vom Anfang des Themas aus entwickeln, mit einer zusammenhängenden Argumentation, ausgearbeiteten Beispielen und späteren Konsequenzen, die auf den Grundlagen aufbauen. Verständliche Sprache ersetzt keine fachliche Substanz.

Dafür wurde erneut recherchiert. Lauf `run_20260913_130920_140788_9f630009` enthält sechs eingelesene Quellen, 331 gespeicherte und 224 ausgewählte Textabschnitte sowie 19 geprüfte Befunde. Fünf Forschungsfragen sind beantwortet, eine teilweise. Neu aufgenommen wurden insbesondere die NYU-Erklärungen zur Motivation, Normierung und Maximum-Likelihood-Lernrichtung. Exakte Langevin-Konvergenzbedingungen und ein Teil der ursprünglichen Contrastive-Divergence-Herleitung bleiben offen. Die neun Modellaufrufe enthalten drei tatsächliche Suchanfragen. Der Quellenreview meldet keine verbleibenden Einwände.

Der neue Skriptlauf `run_20260913_132310_577766_e16772e9` plant drei Folgen. Die erste ist vollständig geschrieben und geprüft: **3.706 Wörter, 53 Sprechersegmente, sieben Kapitel**. Die Planungsschätzung beträgt **28,9 Minuten bei 130 Wörtern pro Minute**, einschließlich Pausen. Bei langsameren 100 Wörtern pro Minute wären es 37,45 Minuten; eine tatsächliche Audiodauer ist noch nicht gemessen. Vor einem späteren Audioexport bleibt die gemessene 30-Minuten-Grenze separat zu prüfen.

Die Folge beginnt mit mehreren plausiblen Fortsetzungen eines Videos. Aus diesem Problem entwickelt sie Energie als Bewertung und Inferenz als Suche. Das Ellipsenbeispiel zeigt konkrete Abstände und einen Gegenfall, in dem eine verschwindende Steigung keinen guten Kandidaten kennzeichnet. Darauf folgen Normierung, Dichte und Wahrscheinlichkeitsmasse, das Lernziel und seine Daten- und Modellbeiträge. Ein Ball mit drei möglichen Ausgängen macht die Lernkorrektur nachvollziehbar. Langevin-Sampling schließt die Trainingsschleife und eröffnet die Frage nach der Verlässlichkeit der erzeugten Beispiele.

- [Überarbeitete erste Folge lesen](../projects/windows-pilot/episodes/ep_001/script.md)
- [Drei aufeinander aufbauende Folgen im Serienentwurf](../projects/windows-pilot/research/series_outline.md)
- [Quellen zur Folge](../projects/windows-pilot/episodes/ep_001/show_notes.md)

Die beiden weiteren Folgen sind bisher geplant: Sampling, Contrastive Divergence und Grenzen überzeugender Bilder; anschließend Score Matching und seine Voraussetzungen. Aiden und Vivian bleiben die Sprecher der ersten Folge. Für deren neuen Plan, Text und Reviews wurden fünf Modellaufrufe benötigt. **Audio ist weiterhin nicht erzeugt oder freigegeben.**

Auch die Erstellungsregeln wurden korrigiert: Fachliche Begriffe sind nach ihrer Erklärung erlaubt; Forschungsbefunde müssen nicht jeweils eine eigenständige Anfängerlektion sein. Spätere Kapitel dürfen auf früher eingeführte Befunde zurückgreifen. Umfang und tatsächliche Erklärungsschritte werden geprüft; ein kurzes Resümee darf keinen Halb-Stunden-Plan erfüllen. Die Testsuite besteht **62 Tests**, einschließlich echter FFmpeg-Verarbeitung. Die Wiederaufnahme des vollständigen Skriptlaufs änderte weder Text, Budget noch Stufenversuche und benötigte keine Modell-, Download- oder TTS-Aufrufe. Lokale Nachweise: `reports/depth-research-verification.json`, `reports/script-depth-revision-verification.json` sowie die beiden Prüfberichte zu den konstruierten Inferenz- und Lernbeispielen.

## Nächste Abnahme

Der Nutzer liest die erste Folge und gibt bei Bedarf Textänderungen vor. Audio wartet auf seinen anschließenden ausdrücklichen Auftrag. Die vorhandenen MP3-Proben dienen weiterhin der Prüfung von Aussprache, Natürlichkeit und beständigen Stimmen. Die technischen Prüfungen belegen erfolgreiche Recherche, Skripterstellung, Qwen-Inferenz und Montage; die Vertonung und redaktionelle Hörprüfung dieser recherchierten Folge stehen noch aus.
