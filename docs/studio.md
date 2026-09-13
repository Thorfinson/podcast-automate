# Podcast Studio im Browser

Im Repository **`Podcast-Studio.cmd` doppelklicken**. Das startet den lokalen Server und öffnet `http://127.0.0.1:8765`. Das Serverfenster bleibt während der Arbeit geöffnet. Die installierte Controller-Umgebung `.venv` wird verwendet; FFmpeg unter `tools/ffmpeg/bin` wird automatisch gefunden. Nach einem frischen Checkout zuerst die Installation aus der README durchführen.

Alternativ ein einzelner Startbefehl:

```powershell
.\.venv\Scripts\pla.exe studio
```

Mit `--port 8766` lässt sich ein anderer Port wählen, mit `--no-browser` das automatische Öffnen unterdrücken. Ein erneuter Doppelklick öffnet das bereits laufende Studio. „Studio beenden“ links unten beendet den lokalen Server und hält seinen aktiven Auftrag an. Der Server ist ausschließlich auf diesem Computer erreichbar. Es gibt keinen externen Webhost, keine Anmeldung am Studio und keine Übertragung der Projektdateien an einen Hostingdienst.

Unter Windows findet das Studio Codex zunächst im `PATH`. Fehlt dieser Eintrag beim Start per Doppelklick, sucht es zusätzlich nach `~/.local/bin/codex.exe` und dem passenden Windows-Programm der installierten OpenAI-Erweiterung in VS Code bzw. VS Code Insiders. Bei mehreren Erweiterungsversionen wird die neueste vorhandene verwendet. Ein ausdrücklich eingetragener Pfad unter `runtime.codex_executable` bleibt maßgeblich. Eine vorhandene Installation muss deshalb nicht allein wegen eines fehlenden `PATH`-Eintrags neu installiert werden. Die ChatGPT-Anmeldung wird anschließend separat mit `codex login status` geprüft. Nach einem behobenen Erkennungsfehler setzt **„Fortsetzen“** den gespeicherten Auftrag fort; ein Entwurf des Inhaltsverzeichnisses bleibt weiterhin vor dem Schreiben der Skripte zur Durchsicht stehen. [Offizielle Codex-Anmeldung](https://learn.chatgpt.com/docs/auth).

## Der geführte Ablauf

1. **Idee & Stimmen:** Thema, Leitfrage, Sprache und Vorwissen eintragen. Unter „Wer spricht deinen Podcast?“ zwischen lokalem Qwen und **Gemini 3.1 Flash TTS über OpenRouter** wählen. Qwen bietet neun Stimmen und startet mit Aiden/Vivian; Gemini bietet 30 Stimmen und startet mit Sadaltager/Aoede. Vorhandene Qwen-Hörproben werden direkt abgespielt. In „Gemini-Stimmen zum Vergleichen“ erzeugt **„Fehlende Hörproben erzeugen · API“** einmalig alle fehlenden Proben in der gewählten Sprache. Neben jeder fertigen Stimme steht **▶ Play**. Die Aufnahmen werden auch in anderen Projekten und nach einem Neustart wiederverwendet; Abspielen braucht keinen Key. Beim Öffnen der Seite wird kein Audio erzeugt.
2. **Mit der Redaktion besprechen:** Nach dem Anlegen des Projekts Wünsche in eigenen Worten formulieren. Das gewählte LLM schlägt einen konkreten Auftrag vor. „In die Felder übernehmen“ lädt den Vorschlag zur Bearbeitung; erst „Änderungen speichern“ ändert das Projekt. Die Redaktion darf keine Freigaben erteilen und behauptet keine bereits durchgeführte Recherche.
3. **Recherche:** Die vorhandene Pipeline sucht im Web, lädt Quellen, erstellt und prüft das Dossier. Das Ergebnis lässt sich auf der Seite lesen. Wissenslücken bleiben sichtbar und können spätere Schritte blockieren.
4. **Inhaltsverzeichnis:** Aus dem Dossier entstehen Folgen, Kapitel, Leitfragen und Erklärungsschritte. Hier endet der Lauf zunächst. Änderungswünsche überarbeiten nur den Plan. „Plan freigeben & Skripte schreiben“ gibt genau diesen Planstand frei und setzt denselben Lauf fort.
5. **Skript lesen:** Lehrkonzept, Schreiben, Dialog-Polishing und unabhängige Prüfungen laufen vor der Leseansicht. Folgen sind einzeln auswählbar. Überarbeite eine Folge mit redaktioneller Rückmeldung; die neue Fassung wird erneut geprüft.
6. **Audio & Export:** Das Kontrollkästchen bestätigt den gelesenen Skriptstand mit dem angezeigten Audioanbieter und den Stimmen. Erst „Audio erzeugen“ startet Qwen oder die kostenpflichtige Gemini-Vertonung. Nach Abschluss stehen Player und MP3-Download bereit. Frühere Aufnahmen werden als solche gekennzeichnet, wenn Text, Anbieter oder Stimmen inzwischen abweichen. Anbieter und Stimmen können für ein vorhandenes geprüftes Skript gewechselt werden, ohne dieses neu zu schreiben.

## Anbieter und Schlüssel

**Text und Audio sind unabhängig wählbar.** Beispielsweise schreibt Codex das Skript und Gemini vertont es über OpenRouter. Bei „Wer schreibt mit?“ bedeutet **Codex** die lokal installierte CLI mit bestehender ChatGPT-Abo-Anmeldung; die Textmodelle laufen nicht offline auf dem PC. Die dort zusätzlich mögliche OpenRouter-Auswahl gilt für Textmodelle mit strukturierten JSON-Antworten. Das Gemini-TTS-Modell wird stattdessen beim Audioanbieter ausgewählt und benötigt keine manuelle Modell-ID. Die belegte Web-Recherche nutzt weiterhin Codex. ElevenLabs ist nicht angebunden.

Für Gemini-Audio ist `google/gemini-3.1-flash-tts-preview` festgelegt. Alle 30 Stimmen stammen aus dem überprüften OpenRouter-Modellkatalog. Die bestehende GPU-Installation ist bei dieser Audioauswahl nicht erforderlich. Die Verbindungskontrolle prüft dann den hinterlegten OpenRouter-Key und überspringt die lokale Qwen-Prüfung. Ein hinterlegter Key ist noch kein erfolgreicher API-Hörtest. [Gemini-Anbindung, Stimmen und Grenzen](gemini-audio.md).

Der API-Key bleibt im Speicher des lokalen Servers und gelangt über die Standardeingabe an den jeweiligen Arbeitsprozess, nicht über Prozessargumente. Er steht weder in Projektdateien noch in Browser-Speichern. Nach einem Neustart neu eingeben; alternativ übernimmt der Server `OPENROUTER_API_KEY` aus seiner Umgebung. „Sitzungs-Key entfernen“ entfernt nur den im Formular eingegebenen Key. Bei vorhandener Umgebungsvariable bleibt deren Key verfügbar.

Eine Textanbieterauswahl wird beim Start eines Skriptlaufs festgehalten. Audioläufe speichern separat Audioanbieter und beide Stimmen. Fortsetzen verwendet jeweils diese gespeicherte Auswahl; Änderungen gelten für neue Läufe. Der API-Key kann ausgetauscht werden. Bezahlte Aufträge starten durch deine Aktionen auf der Seite. Öffnen, Navigieren und Abspielen gespeicherter Qwen- oder Gemini-Proben verbrauchen keine Modellaufrufe; neue Gemini-Proben und Gemini-Vertonungen nutzen dein API-Guthaben. Redaktionelle Gespräche besitzen ein eigenes, dauerhaft gespeichertes Modellaufruflimit aus der Projektkonfiguration.

## Anhalten und Fortsetzen

„Auftrag anhalten“ stoppt den vom Studio gestarteten Arbeitsprozess einschließlich seiner Unterprozesse unter Windows. Fertige Stufen und Qwen-Abschnitte bleiben gespeichert; der gerade laufende Modellaufruf oder Abschnitt muss möglicherweise wiederholt werden. „Fortsetzen“ verwendet die gespeicherten Eingaben. Eine unterbrochene Planung erteilt dadurch keine Skriptfreigabe. Ein unterbrochener Audiolauf benötigt weiterhin seine bereits erteilte passende Freigabe.

Die Stimmenbibliothek wird über „Fehlende Hörproben erzeugen · API“ fortgesetzt. Der Button überspringt alle vollständigen Aufnahmen. Nach einem Anbieterfehler werden keine weiteren Stimmen automatisch angefragt.

Das Studio startet jeweils einen Auftrag. Zusätzlich verhindert die vorhandene Projektsperre Kollisionen mit außerhalb des Studios gestarteten Läufen. Der Stoppknopf steuert ausschließlich eigene Studio-Aufträge. Browser schließen beendet keinen Auftrag. Server beenden hält seinen aktiven Auftrag an; beim nächsten Start bleibt er fortsetzbar. Projekt- und Jobzustände sind unter dem jeweiligen Projekt gespeichert. Für ältere Projekte erscheinen vorhandene Recherche, Skripte und veröffentlichte Audiodateien; ein separat zu prüfender Plan entsteht mit „Inhaltsverzeichnis entwerfen“.

## Stand der Prüfung

Automatisierte Tests prüfen Planfreigaben, veraltete Text-/Stimmenstände, Assistentenvorschläge ohne automatische Projektänderung, Wiederaufnahme, lokale HTTP-Zugriffsschranken, Key-Übergabe, Audio-Downloads mit Suchpositionen sowie UI-Zustände. Vorhandene Pipeline-Tests bleiben aktiv. Die optionalen WebMCP-Lese- und Navigationsfunktionen wurden mit einem Testkontext geprüft, nicht in einem unterstützten Live-Browser. Sie können keine Audio- oder Planfreigabe erteilen.

Am 13.09.2026 wurde die deutsche Gemini-Bibliothek mit allen 30 Stimmen über den lokalen Studio-Server erstellt. Die vorhandene Sadaltager-Aufnahme wurde aus ihrem geprüften Cache übernommen; die übrigen 29 Stimmen wurden auf Nutzerauftrag über OpenRouter erzeugt. Alle 30 MP3-Dateien wurden technisch dekodiert und auf gültige Laufzeiten geprüft. Automatisierte Tests prüfen zusätzlich die Wiederverwendung ohne API-Key, die Fortsetzung nach einem Fehler und Play ohne Generierungsauftrag. Ein vollständiger Durchlauf mit einem neuen Thema und eine Hörprüfung bleiben die praktische Abnahme. Automatisierte Inhaltsreviews garantieren keine hervorragende Erzählung; deine Durchsicht bleibt bewusst Teil des Ablaufs.
