# Gemini-Audio über OpenRouter

Im Studio unter **Auftrag & Stimmen** im Gespräch mit dem redaktionellen Partner **Gemini TTS · OpenRouter** als Audioanbieter und beide Stimmen wählen; unter **Stimmen anhören** lassen sich die Stimmen vergleichen, im **geschützten OpenRouter-Key-Eingang** wird der Key hinterlegt. Die Zusammenfassung mit **Diese Auswahl übernehmen** speichern. Das Schreibmodell kann weiterhin Codex oder Claude sein. Die Audioauswahl wird separat gespeichert und verändert weder das geprüfte Skript noch seine Recherche.

Bei Gemini sind **Sadaltager** für den Experten und **Aoede** für die neugierige Gesprächspartnerin vorbelegt. Das ist eine bearbeitbare Vorauswahl. Die 30 verfügbaren Stimmen wurden am 13. September 2026 anhand von `supported_voices` im öffentlichen [OpenRouter-Modellkatalog](https://openrouter.ai/api/v1/models?output_modalities=speech) geprüft und am 26. September erneut abgeglichen (`speech.VOICES_VERIFIED_ON`):

Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib, Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux, Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia, Sadaltager und Sulafat.

Unter **„Gemini-Stimmen zum Vergleichen“** stehen alle 30 Stimmen. **„Fehlende Hörproben erzeugen · API“** erstellt einmalig eine kurze Aufnahme je fehlender Stimme, auf Deutsch oder Englisch entsprechend der ausgewählten Sprache. Alle lesen denselben Vergleichstext. Der Fortschritt zeigt, wie viele Proben bereits gespeichert sind. Bei einer Unterbrechung bleiben fertige Stimmen erhalten; derselbe Button setzt mit den fehlenden fort. Neue Aufnahmen nutzen OpenRouter-Guthaben.

Neben jeder fertigen Stimme steht **▶ Play**. Dieser Button spielt ausschließlich die gespeicherte MP3 ab, ohne Modellaufruf oder API-Key. Ein weiterer Klick pausiert; der Player unten bietet eine Zeitleiste. Hörproben bleiben über Projektwechsel und Studio-Neustarts erhalten. Eine einzelne fehlende Stimme kann weiterhin über „Hörprobe erzeugen · API“ neben der Rollenauswahl erstellt werden. Laden der Seite und Wechseln von Sprache oder Stimme erzeugen keine Aufnahmen.

Die gemeinsame Bibliothek liegt unter `projects/voice-samples/gemini/` und ist durch die vorhandene Gitignore-Regel für `projects/` ausgeschlossen. Sprache, Stimme, Modell, Vergleichstext und Adapterversion bestimmen die Aufnahme; Dateiprüfsummen verhindern die Wiederverwendung beschädigter Dateien. Bereits vorhandene passende WAV-Aufnahmen aus einzelnen Projekten werden ohne erneuten API-Aufruf übernommen. Deutsch und Englisch besitzen getrennte Bibliotheken.

Für eine Folge anschließend das Skript lesen und unter **Vertonung** genau diesen Text mit dem angezeigten Anbieter und den Stimmen freigeben. Ein Anbieterwechsel übernimmt keine frühere Qwen-Freigabe für kostenpflichtige Gemini-Aufrufe. Bei einer Fortsetzung bleiben Modell, Stimmen und Text des begonnenen Audiolaufs fest; der Key darf erneuert werden. Frühere Aufnahmen bleiben verfügbar und werden als frühere Fassung gekennzeichnet.

## Umsetzung

Die Anbindung verwendet den dokumentierten binären Endpunkt `POST https://openrouter.ai/api/v1/audio/speech`, mit `model: google/gemini-3.8-flash-tts` (wählbar auch `google/gemini-3.8-flash-lite-tts`), dem freigegebenen Text als `input`, einer ausgewählten `voice` und `response_format: pcm`. Sie verwendet keinen Chat- oder JSON-Schema-Aufruf für Sprache. PCM wird entsprechend der Modellspezifikation als 24-kHz-/16-Bit-Mono in WAV gespeichert und anschließend mit der vorhandenen FFmpeg-Montage als MP3 exportiert. [OpenRouter-TTS-Dokumentation](https://openrouter.ai/docs/guides/overview/multimodal/tts), [Modellbeschreibung](https://openrouter.ai/google/gemini-3.8-flash-tts). OpenRouter nimmt für Sprache nur `mp3` und `pcm` an.

Der dokumentierte OpenRouter-Speech-Aufruf hat eine einzelne Stimme pro Anfrage. Daher vertont diese Version die vorhandenen Sprechersegmente mit ihrer jeweiligen Gemini-Stimme und montiert sie in Skriptreihenfolge. **Native Zwei-Sprecher-Anfragen innerhalb eines einzigen Gemini-Aufrufs sind hier noch nicht angebunden.** Googles eigene API unterstützt diesen Modus, ihr separates Anfrageformat wird nicht ungeprüft auf OpenRouter übertragen. [Googles Mehrsprecher-Dokumentation](https://ai.google.dev/gemini-api/docs/speech-generation#multi-speaker).

Es gibt keine redaktionelle Vorgabe von 30–90 Sekunden und keinen erzwungenen Sprecherwechsel. Sehr große Segmente werden allein zum Einhalten der Anfragegröße an Satz- oder Wortgrenzen aufgeteilt; alle Zeichen und die Sprecherzuordnung bleiben erhalten. Zusammenhängende Monologe bleiben Monologe. Text, Sprache, Stimme, Modell und Adapterversion bestimmen den Cache. Unterbrochene Anfragen landen nicht als fertiges Audio im Cache.

Seit 02.10.2026 fragt die Vertonung eine Gateway- oder Überlastantwort (502, 503, 504), eine abgebrochene Übertragung oder eine zurückgesetzte Verbindung zweimal erneut an, nach 3 und 6 Sekunden (`speech.TRANSIENT_RETRIES`, `TRANSIENT_BACKOFF_SECONDS`); vorher hatte ein einzelner 502 unter etwa 1 800 Anfragen eine ganze Folge angehalten. Ein Rate-Limit (429) wartet bis zu sechsmal (`RATE_LIMIT_RETRIES`) die `Retry-After`-Angabe oder eine sich verdoppelnde Pause ab, zusammen etwa drei Minuten. Diese Wartezeit gilt für alle Projekte des Studios gemeinsam: `projects/.gemini_throttle.json` hält sie unter einer Dateisperre (`speech.shared_throttle`), sodass ein 429 in einem Projekt auch die parallelen Aufnahmen und Hörproben der anderen bremst. Bleibt der Fehler bestehen, hält die Vertonung an; fertige Abschnitte bleiben gespeichert, und der Nutzer setzt den Lauf ausdrücklich fort.

Der API-Key bleibt im Arbeitsspeicher und im Authorization-Header. Er wird nicht im Auftrag, Cache oder in Prozessargumenten gespeichert. Weiterleitungen sind gesperrt. Fehlerantworten, leeres Audio, unerwartete Formate und beschädigte Cache-Dateien werden abgefangen. Die technische Audioprüfung kann jedoch nicht beweisen, dass jedes Wort korrekt gesprochen wurde: Bei einem Roh-Audiostream stehen keine verlässlichen Wortzeitmarken oder eine vollständige Transkriptprüfung zur Verfügung. Deshalb bleiben Hörprüfung, Aussprache und mögliche Auslassungen Teil der Abnahme.

## Ausdruck

Jede Gemini-Vertonung erhält vor der Synthese die Stufe **Ausdruck** (so entschieden am 29.09.2026). Das Schreibmodell des Skriptlaufs wählt aus zwölf Arten von Inline-Tags in eckigen Klammern und setzt sie sparsam, höchstens in etwa jedem vierten Abschnitt, zwischen die Wörter. Das sind zum Beispiel ein Schmunzeln an einer überraschenden Wendung, ein Atemzug vor einer Zusammenfassung oder eine kurze Pause vor einer Kernaussage. Erlaubt sind nur `<short pause>`, `<long pause>`, `<breath>`, `<exhales>`, `<sigh>`, `<phew>`, `<laugh>`, `<chuckle>`, `<giggle>`, `<gasp>`, `<tsk>` und `<throat-clearing>`. Google dokumentiert weitere Tags. Diese Auswahl passt zu einem sachlichen Podcast. Am 29.09.2026 wurden alle zwölf auf Deutsch über OpenRouter angehört (`scripts/gemini-tags-test.py`, zweiter Lauf mit allen Zeilen): Sie werden gespielt, nicht vorgelesen, und alle zwölf bleiben freigegeben.

Die Prüfung in `expression.py` hält die Ebene streng. Ohne Tags muss jedes Segment wortgleich mit dem Text sein, der ohnehin gesprochen würde. Zulässig sind höchstens zwei Tags je Segment, höchstens ein Viertel der Segmente je Folge (mindestens drei) und kein Tag innerhalb eines Wortes. Ein `<long pause>` eröffnet seit 02.10.2026 nie ein Segment, weil die Montage dort ohnehin pausiert: Alle 44 solchen Tags der Aufnahmen vom 29.09.2026 standen am Segmentanfang. Die Prüfung entfernt es dort ohne Korrekturaufruf, und ein Segment, das danach kein Tag mehr trägt, fällt aus der Ebene (`expression.without_opening_pause`, Promptversion `audio_expression.v2`). Eine abweichende Antwort geht mit der Beanstandung zweimal zurück. Bleibt sie ungültig, wird die Folge ohne Tags vertont; der Grund steht in `expression.json`. Der Ausdruck ist Feinschliff und hält keine Vertonung an.

**Die Tags gehören zur Leseprüfung.** Das hat der Nutzer am 29.09.2026 so entschieden.

- **Wann sie entstehen:** Sobald ein Skriptlauf fertig ist und das Projekt mit Gemini und Ausdruck vertont wird, setzt das Textmodell des Skriptlaufs die Tags jeder veröffentlichten Folge. Die Folgen laufen dabei parallel, und jede hat ein eigenes kleines Aufrufkontingent.
- **Wo sie liegen:** In `episodes/<folge>/expression.json`, gebunden an den Skripthash, zusammen mit dem gesprochenen Text, auf den sie gesetzt wurden.
- **Wie du sie liest:** Auf **Skripte lesen** stehen die Tags markiert im Text. Weicht die Sprechform eines Segments vom geschriebenen Text ab, stehen sie darunter als „Gesprochen mit Ausdruck: …“.
- **Neu setzen:** Dafür gibt es in der Seitenleiste **Ausdruck setzen** bzw. **Ausdruck neu setzen**, auch **Für alle Folgen**.
- **Bei der Freigabe:** Die Freigabe auf der Seite Vertonung trägt den Hash der gelesenen Tags. Wurden sie danach neu gesetzt, weist das Studio die Freigabe ab, bis die Folge erneut gelesen ist.
- **Was die Vertonung spricht:** Genau diese Tags, ohne neuen Modellaufruf. Ein Segment, dessen Sprechform sich seit dem Setzen geändert hat, geht ohne Tag in die Aufnahme.
- **Wenn noch keine Tags gesetzt sind:** Dann setzt die Vertonung sie wie zuvor selbst, und die Karte sagt, dass sie dann ungelesen sind.
- **Was gleich bleibt:** Skript, Transkript und Freigabetext ändern sich nicht. Eine Aufnahme ohne Ausdruck gilt weiter als aktuell. Wer eine Folge ohne Ausdruck vertonen will, setzt in `studio/audio.json` `"expression": false`.

Eine schriftliche Stilanweisung im Text, etwa „Sag es fröhlich:“, liest Gemini mit vor. Den Sprechstil außerhalb des Texts (`speech_metadata`) reicht der OpenRouter-Endpunkt nicht durch. `scripts/gemini-tags-test.py` erzeugt kurze Vergleichsaufnahmen mit und ohne Tags zum Anhören. Mit `--neu` erzeugt es nur die weiteren Tags ab Zeile 07.

Blockiert die OpenRouter-Datenschutzeinstellung (Zero Data Retention) Google als Anbieter, antwortet der Endpunkt mit 404. Die Vertonung hält dann mit `openrouter_privacy` an, und das Studio nennt die Einstellung unter openrouter.ai/settings/privacy.

## Aufnahmeprüfung, Pausen und Lautheit

Seit 02.10.2026 durchläuft jede Gemini-Aufnahme eine Plausibilitätsprüfung (`speech.take_defect`). Auslöser war eine Aufnahme von 1 248 Zeichen, die auf 0,37 Sekunden abgeschnitten war und unbemerkt exportiert wurde.

- Eine Aufnahme ohne hörbare Sprache fällt durch.
- Jede Stille über 2,5 Sekunden (`HEALTH_SILENCE_SECONDS`) braucht ein Pausen-Tag im gesprochenen Text.
- Ab 80 gesprochenen Zeichen (`HEALTH_MIN_CHARACTERS`; Ausdrucks-Tags zählen nicht) muss die Sprechrate zwischen 8 und 25 Zeichen pro Sekunde liegen (`HEALTH_RATE`). Gemessen wird vom ersten bis zum letzten hörbaren Laut, ohne Pausen über einer Sekunde.

Eine durchgefallene Aufnahme wird einmal neu angefragt. Fällt auch die zweite durch, hält die Vertonung mit `invalid_speech` an und nennt den Abschnitt; fertige Abschnitte bleiben gespeichert, und Fortsetzen nimmt diesen Abschnitt neu auf. Eine Aufnahme aus dem Cache, die die Prüfung nicht besteht, wird verworfen und neu aufgenommen, nie wiederverwendet. Qwen-Aufnahmen durchlaufen diese Prüfung nicht.

In Segmenten mit Pausen-Tag kürzt die Montage Stillen über 1,5 Sekunden auf 1,2 Sekunden (`audio.TRIM_ABOVE_SECONDS`, `TRIM_TO_SECONDS`); ein `<long pause>` hatte in den Exporten vom 29.09.2026 bis zu 7,3 Sekunden Leere hinterlassen. Die gekürzte Zeit steht in `audio_report.json` unter `trimmed_silence_seconds`, andere Segmente bleiben wie aufgenommen.

Die Lautheit wird seit 02.10.2026 mit linearer Verstärkung auf -16 LUFS und einem True-Peak-Limiter mit -2 dBFS Obergrenze (`audio.LIMITER_CEILING_DB`) angeglichen. Der lineare Modus von `loudnorm` war in 29 von 30 Exporten vom 29.09.2026 still auf dynamische Kompression ausgewichen. `audio_report.json` hält das Verfahren (`loudness_mode: linear_gain_limiter`), die Verstärkung und die am fertigen MP3 gemessene Lautheit und Spitze fest (`output_loudness`).

Eine Folge über 30 Minuten wird in Teilen vertont. Der Hörprüfbogen `listening_sheet.md` nennt dann in einer eigenen Spalte den Teil jeder Zeile, weil die Zeiten jedes Teils bei 0:00 beginnen.

## Prüfung und verbleibende Praxisprobe

Automatisierte Tests prüfen den Speech-Aufruf und seine Stimmen, WAV-Erzeugung, Aufteilung ohne Textverlust, Cache-Wiederverwendung, API-/Formatfehler, die Plausibilitätsprüfung der Aufnahmen, Wiederholungen nach Gateway-Fehlern und die gemeinsame Rate-Limit-Wartezeit (`tests/test_speech.py`), Lautheit und Pausenkürzung (`tests/test_audio.py`), unveränderte Skripte, getrennte Anbieterwahl, Freigaben und Wiederaufnahme nach einem API-Limit. Die bestehende FFmpeg-Montage wird dabei mit simulierten API-Audiodaten ausgeführt. Qwen-Aufrufe sind in den Gemini-Integrationstests ausdrücklich gesperrt; der Textaufruf der Stufe Ausdruck ist simuliert (`tests/test_expression.py`).

Am 13.09.2026 wurde die deutsche Hörprobenbibliothek mit allen 30 Stimmen über OpenRouter erzeugt; alle Dateien wurden technisch dekodiert und auf gültige Laufzeiten geprüft ([Stand der Prüfung](studio.md#stand-der-prüfung)). Ein Geschwindigkeitsvergleich zwischen Gemini und der lokalen Qwen-Vertonung ist in keinem Dokument festgehalten; Gemini vermeidet die lokale GPU-Vertonung, eine konkrete Beschleunigung ist damit nicht belegt. Die Hörprüfung der erzeugten Stimmen und Folgen bleibt eine menschliche Aufgabe.
