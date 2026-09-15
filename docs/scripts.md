# Vom Dossier zum lesbaren Dialog

Im Studio lassen sich Textmodell und Reasoning-Stufe unter **Auftrag & Stimmen** wählen. Für einen neuen Codex-Skriptlauf per Einzelbefehl entsprechen dem `--model gpt-6-astra --reasoning-effort xhigh`; angeboten werden `low`, `medium`, `high` und `xhigh`. Beide Werte werden gespeichert und beim Fortsetzen übernommen. Eine abweichende Auswahl benötigt einen neuen Lauf. Bei OpenRouter ist die Reasoning-Stufe optional und muss vom gewählten Modell unterstützt werden. [Modellauswahl im Studio](studio.md#textmodell-und-denkaufwand-auswählen).

`pla script` erstellt aus einem abgeschlossenen Recherchelauf einen Serienentwurf, quellengeprüfte Lehrpläne und Dialogskripte. Die Stufen sind `planning`, `teaching`, `writing`, `polishing`, `review` und `publish`. Der Befehl endet beim lesbaren Text. Audio wird nicht erzeugt. [Lehrplanung und verbindliche Qualitätsprüfungen](teaching-design.md).

```powershell
# Zuerst eine vollständige Recherche, sofern noch nicht vorhanden:
.\.venv\Scripts\pla.exe research .\projects\windows-pilot

# Die erste Folge für die Leseprüfung vorziehen:
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --episode ep_001

# Fortschritt und Wiederaufnahme desselben Auftrags:
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot
```

Ohne `--episode` werden die Skripte aller Folgen des neu erstellten Entwurfs geschrieben. Die Folgenzahl ergibt sich aus dem Inhalt; eine einzelne Folge bleibt in der Planungsschätzung bei 130 Wörtern pro Minute einschließlich Pausen unter 30 Minuten. Ein neuer `script`-Aufruf plant neu. `resume` verwendet dagegen den gespeicherten Plan und gültige Entwürfe weiter.

## OpenRouter für einen Skriptlauf

Standard bleibt die Codex-Abo-Verbindung. Optional können Planung, Lehrplanung, Schreiben, Dialog-Polishing und sämtliche Modellreviews dieses Skriptlaufs über OpenRouter laufen. Der Key wird nur für den aktuellen Prozess verwendet. Der Recherchebefehl bleibt bei Codex; Spracherzeugung erfolgt weiterhin separat mit Qwen.

```powershell
# anbieter/modell-id durch eine OpenRouter-Modell-ID mit JSON-Schema-Unterstützung ersetzen.
# --api-key ohne Wert fragt den Key verdeckt im Terminal ab.
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --episode ep_001 --backend openrouter --model "anbieter/modell-id" --api-key

# Gleichen Lauf fortsetzen: Anbieter, Modell und Tokenlimit werden wiederverwendet.
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot --run-id "RUN_ID" --api-key
```

`--api-key "DEIN_KEY"` wird ebenfalls unterstützt. Ein direkt ausgeschriebener Key kann jedoch in Shell-Verlauf und Prozessargumenten stehen. Die verdeckte Eingabe vermeidet dies; alternativ liest der OpenRouter-Adapter `OPENROUTER_API_KEY`. Ein ausdrücklich übergebener Key hat Vorrang. Keys gehören nicht in `project.yaml`; sie werden weder in Prompts noch Projekt-, Antwort- oder Protokolldateien geschrieben. Auch beim Start von Codex wird `OPENROUTER_API_KEY` aus dessen Umgebung entfernt. Ohne sichere Terminaleingabe bricht die verdeckte Abfrage ab, statt den Key sichtbar einzulesen.

OpenRouter-Aufrufe werden über dessen API-Guthaben abgerechnet. Die Anwendung fordert [strukturierte JSON-Schema-Ausgaben](https://openrouter.ai/docs/guides/features/structured-outputs) und passende Anbieter an. Sie bevorzugt für längere Texte Anbieter mit hohem [Token-Durchsatz](https://openrouter.ai/docs/guides/routing/provider-selection#provider-sorting). Die tatsächliche Geschwindigkeit hängt vom gewählten Modell, Anbieter und der Auslastung ab; die Qualitätsprüfungen werden vollständig ausgeführt.

`--max-output-tokens 32768` ist der Standard pro OpenRouter-Aufruf. Bei einem Modell mit kleinerem Ausgabelimit einen passenden Wert wählen; zu knappe Limits können einen vollständigen Episodentext abschneiden. Abgeschnittene, abgewiesene oder ungültige Antworten werden nicht als fertiger Text übernommen. Anbieter, Modell, Tokenlimit und Adapterversion gehören zu den gespeicherten Eingaben. Änderungen daran benötigen einen neuen `script`-Lauf, optional mit `--revise`; ein rotierter Key benötigt keinen neuen Lauf. `--revise` akzeptiert dieselben Anbieteroptionen.

`reports/script_quality.yaml` nennt die Auswahl unter `text_generation`. Erfolgreiche Aufrufe speichern angefordertes und gemeldetes Modell, Anbieter, Laufzeit, Tokenverbrauch und gemeldete USD-Kosten unter `runs/<run_id>/calls/call_*/metadata.json`. Fehlende Kostenangaben bedeuten unbekannte Kosten, nicht kostenlose Nutzung; diese Metadaten ersetzen keine OpenRouter-Abrechnung. Fehlende Credits und Anfragelimits pausieren den Lauf. Netzwerk- und andere API-Fehler werden mit einem handhabbaren Fehler gespeichert; `resume` verwendet bereits fertige Arbeit weiter. Es gibt keine automatischen erneuten kostenpflichtigen Anfragen nach einem Verbindungsabbruch.

## Erklärweise und Leseprüfung

Die erste Folge baut ihre zentrale Frage ohne Vorwissen auf. Vertraute Situationen und zusammenhängende mentale Bilder führen durch ein ausgearbeitetes Beispiel. Die Erklärungen zeigen, was sich verändert, warum ein Schritt hilft und wo die Grenzen liegen. Beide Stimmen tragen sinnvoll zum Gespräch bei. Formeln, unerklärte Begriffe, Quellen-IDs und Regieanweisungen gehören nicht in den gesprochenen Text.

Der gewünschte Ton ist klar und erwachsen, ohne übermäßige Vereinfachung. Nötige Begriffe werden knapp eingeführt und danach selbstverständlich verwendet. Ein guter Vergleich darf stehenbleiben, ohne ihn ständig neu zu erklären oder als erfunden zu kennzeichnen. Wiederholungen und mehrfache Rückblicke werden zugunsten eines natürlichen Gesprächsflusses gekürzt.

Längere Monologe sind ausdrücklich erlaubt, wenn sie einen Gedanken zusammenhängend entwickeln. Ein Wechsel braucht einen inhaltlichen Anlass: Einwand, Ergänzung, Prüfung einer Vermutung oder neue Perspektive. Satzlängen dürfen variieren; kurze Reaktionen oder Selbstkorrekturen sollen der Erklärung dienen. Erzwungenes Pingpong, künstliche Begeisterung und eingestreute Füllwörter sind kein Qualitätsziel. Es gibt keine feste Dauer von 30–90 Sekunden pro Beitrag.

Jede Folge bekommt ein gesprochenes Intro mit kurzer Begrüßung, Einordnung und Übergang zur Einstiegsfrage. Ihr Outro beantwortet diese Frage, setzt bei einer tatsächlich geplanten Nachfolgefolge einen passenden Ausblick und verabschiedet die Hörer. Ein fachliches Beispiel am Anfang und eine offene Frage am Ende allein reichen dafür nicht. Die erste Folge führt zusätzlich das **Gesamtthema, seine Bedeutung und den Weg durch die Reihe** ein. Die letzte Folge endet mit einer **Zusammenfassung und Synthese der gesamten Reihe**: Wie bauen die Erkenntnisse aufeinander auf, wie beantworten sie die gemeinsame Ausgangsfrage und welche Grenzen bleiben? Bei einer einzelnen Folge werden diese Aufgaben zusammengeführt.

Planung, Lehrkonzept, Schreiben und Polishing erhalten dafür die Position im vollständigen Serienplan, die zentrale Frage und den geplanten Themenweg – auch wenn nur eine einzelne Folge erzeugt wird. Die neue Planung ordnet der Schlussfolge die Belege für ihren Gesamtrückblick zu. Intro und Outro bleiben innerhalb der vorhandenen ersten und letzten Kapitel und des Wortbudgets. Es gibt keine feste Länge, erfundenen Sendungsnamen, automatisch aus TTS-Stimmen abgeleiteten Host-Identitäten oder Werbeformeln. Nichtfachliche Begrüßungen brauchen keine Quellen; fachliche Rückblicke schon.

Technisch vertont Qwen jeden gespeicherten Sprecherabschnitt einzeln und verwendet unveränderte Audiodateien aus dem Cache wieder. Ein längerer Redebeitrag kann in mehrere Abschnitte desselben Sprechers geteilt werden. Die Montage ordnet diese Abschnitte an und setzt Pausen. Echte Sprecherüberlappungen, szenenweite Regieanweisungen und Gemini-/ElevenLabs-TTS sind derzeit nicht implementiert. Die gesamte Folge wird nicht in einem einzigen TTS-Aufruf erzeugt.

Zugänglichkeit begrenzt die fachliche Tiefe nicht. Bei einem universitären Anspruch führt die erste Folge vom Ausgangsproblem bis zum eigentlichen Mechanismus und seinen weiterführenden Konsequenzen. Jeder Abschnitt beantwortet eine aus dem vorherigen entstandene Frage. Ein ausgearbeitetes Beispiel, begründete Zwischenschritte und Gegenbeispiele machen die Argumentation nachvollziehbar. Fachbegriffe wie Gradient oder Normierung sind erlaubt, sobald die Bedeutung erklärt wurde. Eine Liste von Definitionen oder eine lange Analogie ersetzt diese Entwicklung nicht.

Der Nutzer möchte **das Skript zuerst lesen und danach über Audio entscheiden**. `episodes/audio_review.yaml` hält deshalb den aktuellen Skriptstand mit `audio_approved: false` fest. Ein Modellreview erteilt keine Nutzerfreigabe. Stimmen im Windows-Pilot sind Aiden (`host_a`) und Vivian (`host_b`).

## Ergebnisse

| Datei | Inhalt |
| --- | --- |
| `models/knowledge_model.yaml` | Unverändert übernommene belegte Befunde, Begriff-/Mechanismus-/Beispiel-IDs, Erklärabhängigkeiten und offene Fragen |
| `models/series_plan.yaml` | Begründeter Erklärweg, Folgen, Szenen, Voraussetzungen und vertagte Themen |
| `research/series_outline.md` | Lesbarer Überblick; unterscheidet geplante Folgen von hier geprüften Skripten |
| `episodes/ep_001/episode_plan.yaml` | Frage, Szenen und benötigte Befunde der Folge |
| `episodes/ep_001/teaching_plan.md` und `.yaml` | Lernziele, Voraussetzungen, ausgearbeitetes Beispiel und Synthese |
| `episodes/ep_001/script.yaml` | Kanonische Sprechersegmente mit Wissensreferenzen |
| `episodes/ep_001/script.md` | Derselbe Dialog als lesbarer Text mit den gewählten Stimmen |
| `episodes/ep_001/show_notes.md` | Kapitel, Quellenlinks und offene Vertiefungen |
| `reports/script_quality.yaml` | Quellen- und Strukturprüfung, Leserantworten, belegte Lehrprüfung, Wortzahl und geschätzte Sprechzeit |
| `episodes/audio_review.yaml` | Skripthashes und ausstehende Leseprüfung vor Audio |

Die Dateien liegen im privaten Projektordner und sind von Git ausgeschlossen. `runs/<run_id>/` hält Eingaben, Modellantworten, Entwürfe und Reviews für die Wiederaufnahme fest. Quellen werden für diese Stufe nicht erneut heruntergeladen.

`script.md` ist die erzeugte Leseansicht; `script.yaml` ist der kanonische Text. Manuelle Änderungen am kanonischen Skript werden bei `resume` erkannt und nicht überschrieben. Sie benötigen eine erneute fachliche Prüfung vor der Fortsetzung.

## Eigener Dialog-Polishing-Schritt

Nach `writing` verarbeitet ein neuer Modellaufruf den vollständigen Entwurf. Er erhält Text, Lehrplan, Publikum und Rollenverteilung. Seine Aufgabe ist die sprachliche und dramaturgische Ausarbeitung: verständliche Sätze, passende Reaktionen und Übergänge, die aus dem Gedankengang entstehen. Er darf keine zusätzlichen Fakten, Zahlen oder Beispiele erfinden und keine notwendige Herleitung oder Einschränkung streichen.

Die ursprünglichen Absatzgrenzen und Sprecherzuordnungen sind dabei veränderbar. Der Polishing-Aufruf soll zunächst den Gesprächsfluss eines ganzen Kapitels beurteilen und dichte Erklärungen bei Bedarf neu gruppieren. Viele umformulierte Sätze allein belegen keine gute Überarbeitung. Der Vergleich prüft auch schwierige Passagen und Übergänge auf unklare Bezüge, bloßes Nebeneinander von Erklärungen und inhaltsleere Wiederholungen. Es gibt weiterhin keine Quote für neue Segmente oder Sprecherwechsel.

Die Rollen werden unabhängig von der TTS-Stimme festgelegt:

- `host_a`: der ruhige, präzise Experte. Er entwickelt Mechanismen, liefert relevante Details und beantwortet den konkreten Einwand.
- `host_b`: die neugierige, mitdenkende Gesprächspartnerin. Sie formuliert naheliegende Zweifel, prüft eine Annahme und fragt nach Bedeutung oder Konsequenzen. Sie darf selbst Schlüsse ziehen und muss keine künstliche Unwissenheit vorspielen.

Für Aiden und Vivian gilt diese Aufteilung ebenso. Längere zusammenhängende Erklärungen sind erlaubt; Sprecher müssen sich weder ständig abwechseln noch gleich viel sprechen. Eine plausible Frage schafft einen Anlass für die folgende Erklärung. Ein allgemeines „Spannend, erzähl mehr“ erfüllt diese Rolle nicht.

Ein separater Vergleichsaufruf prüft `meaning`, `completeness`, `speaker_roles`, `spoken_language` und `episode_framing`. Das letzte Kriterium prüft Intro und Outro einschließlich Serieneinstieg beziehungsweise Gesamtabschluss. Jedes positive Urteil braucht tatsächliche Textbelege; für Bedeutung und Vollständigkeit aus beiden Fassungen, für die Rahmung aus dem ersten und letzten Kapitel. Fehlende Kriterien, erfundene Belege oder fortbestehende Einwände blockieren die weitere Verarbeitung. Bis zu zwei Reparaturen sind möglich; Wiederaufnahme erhält Kandidat, Vergleich und Versuchszähler. Im Normalfall kommen pro Folge zwei Modellaufrufe hinzu. Sie zählen zum bestehenden Budget und laufen über den gewählten Textanbieter.

Unter `runs/<run_id>/polishing/<episode_id>/` stehen `before.md`, `after.md`, `script.json`, `review.json`, `result.json` und `checkpoint.json`. Der ursprüngliche Entwurf bleibt unter `drafts/` erhalten. `reports/script_quality.yaml` übernimmt den Vergleich unter `episodes.<episode_id>.dialogue_polish` und benennt die Rollen. Die dortigen Belege gelten für die Fassung direkt nach dem Polishing. Anschließende fachliche Reparaturen können sie noch verändern; maßgeblich für Audio bleibt ausschließlich das abschließend geprüfte und vom Nutzer freigegebene `script.yaml`.

Danach prüfen Quellenreview, Leser und Lehrprüfer den tatsächlich überarbeiteten Text. Der Quellenreview sieht zusätzlich den ursprünglichen Entwurf und die Rollen, damit auch spätere Reparaturen die Erklärungen und Gesprächsführung erhalten. Ein bestandener Vorher-/Nachher-Vergleich behauptet keine fachliche Wahrheit des ursprünglichen Texts. Diese wird weiterhin gegen die Quellen geprüft. Der abschließende Export setzt die Audiofreigabe wieder auf ausstehend.

Die Intro-/Outro-Vorgaben gelten für neu ausgeführte Schritte. Ein bereits laufender Prozess verwendet seinen geladenen Code weiter; ein alter bestandener Vergleich gilt nicht nachträglich als Prüfung der Rahmung. Bestehende Texte benötigen dafür eine gezielte Überarbeitung mit erneuter Prüfung. `result.json` nennt die verwendete Promptversion. Die Eingaben und Freigaben laufender Aufträge werden durch diese Ergänzung nicht umgeschrieben.

Nach einer Korrektur der redaktionellen Prüfversion wird ein gespeichertes Urteil beim Fortsetzen neu geprüft. Der zuletzt überarbeitete Text und die bereits verbrauchten Korrekturrunden bleiben erhalten. Die einzelnen Lese- und Lehrprüfungen sind an ihren jeweiligen Prompt gebunden; alte Urteile ohne diese Bindung werden nicht übernommen. Das erzeugt keine automatische Freigabe und setzt weder Versuchszähler noch Modellbudget zurück. Konkrete Einwände der Abschlussprüfung stehen im Studio unter **Ausarbeitung**.

Bestehende Skriptfassungen werden nicht automatisch umgeschrieben. Für die neue Stufe einen neuen `script`-Lauf oder `--revise` verwenden. Ältere Skriptläufe können nach dem Versionswechsel nicht durch `resume` nachträglich als poliert gelten. Bereits gestartete Audioaufträge bleiben an ihren gespeicherten Text gebunden.

## Einen vorhandenen Text überarbeiten

```powershell
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --revise ep_001 --feedback "Direkter formulieren und Wiederholungen kürzen."
```

Die Überarbeitung beginnt einen neuen Lauf mit dem bisherigen Serienplan und dem vorhandenen kanonischen Skript. Der alte Text und die Rückmeldung werden als Eingaben gespeichert; die Recherche bleibt erhalten. Neue Planungs- oder Suchaufrufe sind dafür nicht nötig. Stiländerungen in `project.yaml` werden angewendet. Das überarbeitete Skript muss erneut Quellen-, Struktur- und Erklärprüfung bestehen. Danach warten der neue Text und sein eigener Hash wieder auf die Leseprüfung vor Audio. Bei einem geänderten Rechercheauftrag oder beschädigten Plan ist stattdessen ein neuer regulärer Skriptlauf nötig.

Der Lehrplan wird auch bei `--revise` neu erstellt und geprüft. Wenn die bisherige Reihenfolge selbst ungeeignet ist, einen regulären `script`-Aufruf für eine neue Planung verwenden. Alte Skriptläufe aus der Zeit vor der Stufe `teaching` werden durch `resume` nicht als nachträglich lehrgeprüft ausgegeben; sie benötigen einen neuen Skriptlauf.

## Prüfungen und Grenzen

Der Quellenlauf muss vollständig sein; seine gespeicherten Dateien müssen zu den Prüfsummen passen. Der Plan darf keine Befunde erfinden, jeder Befund erhält einen Platz oder eine begründete Auslassung, und Erklärabhängigkeiten dürfen keinen Kreis bilden. Ein Skript muss seine geplanten Befunde referenzieren und Kapitel sowie Sprecher korrekt zuordnen. Spätere Kapitel dürfen bereits eingeführte Befunde wieder aufgreifen und darauf aufbauen; noch nicht eingeführte Befunde bleiben ausgeschlossen. Die Quellenzuordnung soll eine zusammenhängende Argumentation ermöglichen.

Ein separater Modellaufruf prüft das tatsächliche Gesagte gegen die zugeordneten Befunde und Quellenabschnitte. Er prüft außerdem den verlangten Anspruch, die ausgearbeiteten Erklärungsschritte, den Zusammenhang von Anfang und Schluss, Verständlichkeit, Beispiel, Metapherngrenzen und Dialog. Bis zu drei Überarbeitungen sind möglich. Verbleibende Einwände blockieren die Übernahme; Entwürfe und Einwände bleiben gespeichert. Auch nach einer Abo-Pause muss ein bereits korrigierter Text nicht erneut geschrieben werden. Alle Modellaufrufe zählen gegen das konfigurierte `research_limits.model_calls`-Budget dieses Skriptlaufs.

Zusätzlich beantwortet ein frischer Leseraufruf die Lernfragen nur aus dem Dialog, ohne Musterlösungen. Eine getrennte redaktionelle Prüfung erhält ausschließlich Publikum, Anspruch und Text; sie sieht weder Lehrplan noch Urteile der anderen Prüfer. Ein weiterer Prüfer bewertet alle sieben Lehrkriterien und jedes Lernziel anhand tatsächlicher Textbelege und ordnet jede vom Leser gemeldete Lücke ausdrücklich ein. Die Anwendung kontrolliert die Belege und die Vollständigkeit der Prüfungen. Ein fehlender erforderlicher Erklärungsschritt oder negatives Urteil führt zur Überarbeitung und bei fortbestehenden Problemen zur Blockierung. Fehlende Quellen für notwendige Grundlagen werden bereits vor dem Schreiben als konkrete Recherchefragen gespeichert.

Die Sprechzeit ist eine Schätzung aus Wortzahl und geplanten Pausen: Planungswert 130 Wörter pro Minute, zusätzlich eine langsame Vergleichsschätzung mit 100. Ein Skript unter 85 Prozent seiner geplanten Dauer wird zur inhaltlichen Überarbeitung zurückgegeben. Diese Prüfung erkennt ein grobes Verfehlen des Umfangs; sie beweist keine Erklärungstiefe. Die tatsächliche Länge steht erst nach der Spracherzeugung fest und muss vor einem späteren Audioexport separat gegen die 30-Minuten-Grenze geprüft werden. Die langsame Vergleichsschätzung ist keine gemessene Dauer und begrenzt den Text nicht zusätzlich. Ein bestandener Modellreview ersetzt weder die Leseprüfung noch die Hörprüfung. Der Serienentwurf ist eine redaktionelle Planung; ein vollständiger Review über alle Folgengrenzen und die Produktion der ganzen Serie bleiben weitere Ausbauschritte. Ein öffentliches Veröffentlichen findet nicht statt.
