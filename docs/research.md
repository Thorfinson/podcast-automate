# Themenrecherche und Dossier

Die erweiterten Belegverträge (`evidence.v1`) prüfen jeden Befund, Quellenrollen und Unabhängigkeit, Synthesevergleiche sowie die Erhaltung von Geltungsbereich und Aussagekraft bis ins Skript. [Umsetzung, Artefakte und Prüfgrenzen](research-evidence.md).

`pla research <project-dir>` setzt den Weg **Thema → Live-Suche → abgerufene Quellen → belegtes Dossier** um. Das Thema steht in `project.yaml`; vorbereitete Quelldateien sind keine Voraussetzung. Optionale `seed_urls`, `seed_people`, `focus_questions` und `local_sources` ergänzen den Auftrag. Relative lokale Quellpfade beziehen sich auf den Projektordner.

## Starten und fortsetzen

Vor dem Lesen erhält ein neuer Fragenplan eine separate Umfangsprüfung. Jede Aufgabe muss einen abgegrenzten Gegenstand haben; ihre Abschlusskriterien prüfen dieselbe Antwort. Unabhängige Methoden, Mechanismen oder empirische Vergleiche werden aufgeteilt, auch wenn sie denselben Autor oder dieselbe Leitfrage betreffen. Ein zusammenhängender Mechanismus und Design, Ergebnisse und Grenzen einer einzelnen Studie bleiben zusammen. Eine Aufteilung muss alle bisherigen Kriterien und Quellenzuordnungen erhalten. Die Prüfung ist auf zwei Durchgänge begrenzt und verwendet das gewählte Textmodell sowie das normale Auftragsbudget. Kleinere Aufgaben bedeuten keinen erweiterten Themenumfang, können aber mehr einzelne Modellaufrufe benötigen.

Die Controller-Umgebung benötigt die aktuellen Projektabhängigkeiten einschließlich `pypdf[fonts]`. Installation nur ausführen, wenn kein anderer `pla`-Prozess die Windows-Programmdatei verwendet:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pla.exe research .\projects\windows-pilot
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot
```

Für maschinenlesbare Ausgaben jeweils `--json` ergänzen. `resume --run-id <run_id>` wählt einen vorhandenen Lauf. Änderungen an `project.yaml` oder lokalen Quelldateien benötigen einen neuen Recherchelauf. Die Recherche braucht weder FFmpeg noch Qwen; das Thema wird hier noch nicht gesprochen.

Wenn sich nur die Erklärweise oder Zielgruppe ändert, können die bereits geprüften Quellen für ein neues Dossier dienen:

```powershell
.\.venv\Scripts\pla.exe research .\projects\windows-pilot --reuse-sources <run_id>
```

Dieser neue Lauf übernimmt eine geprüfte Kopie der Quellen und dokumentiert den ursprünglichen Suchlauf samt Abrufdatum. Dossier und Qualitätsprüfung werden neu erstellt. Falls die übernommenen Quellen nicht alle Qualitätsmerkmale erfüllen, recherchiert auch dieser Lauf automatisch nach. Geänderte Forschungsfragen, beschädigte Dateien, veränderte lokale Quellen oder ein unpassender Parserstand verhindern die Übernahme.

## Erklärweise

Der Standard setzt keine fachlichen oder mathematischen Vorkenntnisse voraus. Jede wichtige Idee wird in Alltagssprache aufgebaut: vertraute Situation, klares mentales Bild, einzelne Schritte und deren Wirkung. Nötige Begriffe folgen erst nach der Erklärung. Der Auftrag steht dauerhaft in `audience_level`, `prior_knowledge` und `depth_request` des Projekts; die Dossier- und Review-Anweisungen wenden ihn an.

Eine Formel lediglich in Wörter umzuschreiben erfüllt diese Vorgabe nicht. Der Text erklärt, was ein Schritt tut, warum er hilft und wo er scheitern kann. Bei universitärem Anspruch hält das Dossier auch die begründenden Zwischenschritte fest, die Grundlagen und fortgeschrittene Mechanismen verbinden. Nötige Konzepte wie Gradient und Normierung werden erklärt, nicht pauschal ausgeschlossen. Eine intuitive Herleitung ersetzt keinen formalen Beweis; tatsächlich fehlende Belege bleiben als Lücke sichtbar.

Ein erfundenes Bild steht getrennt vom belegten Befund in `illustration`. Dazu gehört immer `illustration_limit`: Wo hilft der Vergleich nicht mehr? Im lesbaren Dossier erscheinen beide als „Bild zum Mitdenken“ und „Grenze des Bildes“. Der Review prüft auch verständliche Sprache und irreführende Vergleiche. Originaltitel und kurze Belegzitate bleiben im Quellenteil erhalten; sie sind kein gesprochener Podcasttext.

## Verarbeitung

1. **Discovery:** Codex leitet Fragen und Suchanfragen ab und sucht live nach zugänglichen Primärquellen. Die Anwendung prüft tatsächliche Suchereignisse im CLI-Protokoll; eine bloße Modellbehauptung reicht nicht.
2. **Retrieval:** Die Anwendung lädt HTML, PDF oder Text selbst, speichert die Rohdateien und extrahiert Abschnitte mit stabilen IDs. PDFs behalten Seitenreferenzen. Doppelte URLs und identische Texte werden erkannt; Zugriffsfehler bleiben im Bericht.
3. **Feste Recherchefragen:** Ein Arbeitsplan zerlegt die ursprünglichen Leitfragen in getrennt prüfbare Teilfragen mit Abschlusskriterien. Jede ursprüngliche Leitfrage und jede bereits gespeicherte Beleglücke muss zugeordnet sein. Definitionen und empirische Bestätigung werden getrennt behandelt; das Nachschlagen eines Begriffs verlangt keinen erfundenen Wirksamkeitsnachweis.
4. **Lesen und Antworten:** Jede Teilfrage bekommt passende Originalabschnitte. Das Modell kann gezielt im gesamten gespeicherten Bestand oder innerhalb einer Quelle suchen, weitere Trefferseiten öffnen und benachbarte Abschnitte nachlesen. Erst bei fehlendem Material sucht es neue Quellen im Web. Eigene Notizen bleiben Hinweise; sie können eine Antwort nicht allein belegen.
5. **Einzelprüfung:** Ein separater Modellaufruf prüft die Antwort gegen ihre festen Kriterien und tatsächlich gelesenen Belege. Bestandene Antworten werden mit Quellen- und Antwortprüfsummen gespeichert. Bei Unterbrechung bleiben diese Abschlüsse erhalten.
6. **Dossier und Gesamtprüfung:** Erst aus den geprüften Antworten entsteht das Dossier. Bei einem übernommenen Entwurf werden die betroffenen Befunde gezielt ergänzt. Quellenprüfung und eine unabhängige Bewertung aller ursprünglichen Leitfragen bleiben erforderlich. Konkrete Einwände werden den betroffenen Teilfragen zugeordnet; gemeinsam zu einer Leitfrage gehörende Antworten werden nicht pauschal wieder geöffnet.
7. **Export:** Erst nach bestandenen Einzel- und Gesamtprüfungen werden Dossier und Begleitdateien als aktueller Projektstand bereitgestellt. Forschungsartefakte und Rohquellen bleiben lokal von Git ausgeschlossen.

Die CLI verwendet `web_search="live"`, strukturierte Ausgaben und die vorhandene ChatGPT-Anmeldung. Für diese Aufrufe werden die Benutzerkonfiguration und Shell-Werkzeuge ausgeschaltet; ein in `runtime.codex_model` ausdrücklich gesetztes Modell wird übergeben. Geprüft wurde Codex CLI `0.154.0-alpha.6.2`. [Offizielle Konfigurationsreferenz](https://learn.chatgpt.com/docs/config-file/config-reference) und [CLI-Befehlsreferenz](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

## Ergebnisse

| Datei | Inhalt |
| --- | --- |
| `research/research_plan.yaml` | Forschungsfragen, Suchanfragen, Umfang und Limits |
| `research/source_candidates.yaml` | Gefundene URLs, Auswahlgründe und Abrufprobleme |
| `sources/raw/<run_id>/` | Tatsächlich heruntergeladene oder importierte Quelldateien |
| `sources/processed/<run_id>/` | Extrahierte Texte, Abschnitte, Seiten, Metadaten und Hashes |
| `models/source_index.yaml` | Strukturierter Index der eingelesenen Quellen |
| `research/dossier.yaml` | Befunde, Belege und Frageabdeckung |
| `research/research_briefing.md` | Lesbares Dossier mit Zitaten und Quellenlinks |
| `research/open_questions.md` | Offene Fragen und Lücken |
| `research/questions.md` und `questions.json` | Geprüfte Einzelantworten, feste Abschlusskriterien und Quellen |
| `runs/<run_id>/research_questions.json` und `.md` | Laufender, auch bei Unterbrechung lesbarer Stand jeder Teilfrage |
| `runs/<run_id>/question_research/` | Arbeitsplan, Leseschritte, Antwortprüfungen, Abrufbelege und Wiederaufnahme |
| `research/quality.md` | Qualitätsmerkmale und begründete Bewertung jeder ursprünglichen Leitfrage |
| `reports/research_quality.json` | Prüfstatus, Grenzen, Quellzahl und verbrauchtes Laufbudget |
| `runs/<run_id>/` | Manifest, Zwischenstände, Suchereignisse, Modellmetadaten und Reparaturbefunde |

Ein Quellenkandidat ist noch kein Beleg. Nur eingelesene Abschnitte, die dem schreibenden Modell tatsächlich vorlagen, dürfen zitiert werden. Publikationsangaben können teilweise aus Suchergebnissen stammen und sind als noch zu prüfende Metadaten gekennzeichnet.

## Korpusprobe der Lücken

Jede gemeldete Lücke wird ohne Modellaufruf gegen die bereits gespeicherten Abschnitte geprüft. Die
Probe nutzt dieselbe lexikalische Suche wie das Nachlesen: Aus dem Lückentext werden Schlüsselwörter
gebildet; ein Abschnitt zählt als Treffer, wenn mindestens zwei verschiedene davon als ganze Wörter
darin vorkommen. Gezählt werden nur ganze Wörter des Abschnitts, nicht Teilzeichenketten: „rule“ in
„overruled“ oder „load“ in „download“ zählt nicht, auch wenn die Rangfolge der Lesesuche solche
Vorkommen weiterhin berücksichtigt. Häufige deutsche Funktionswörter (etwa „sie“, „beim“, „unter“,
„dabei“) sind keine Schlüsselwörter. Die Ergebnisse stehen in
`runs/<run_id>/question_research/gap_probes.json` und im Qualitätsbericht unter „Korpusprobe der
Lücken“.

Die Probe vergleicht Wörter. Steht die Lücke auf Deutsch und die Quelle auf Englisch, findet der
Lückentext allein den passenden Abschnitt nicht; `no_hits` heißt dann „keine lexikalische
Überschneidung“, nicht „nicht vorhanden“. Deshalb trägt jede nicht beantwortete Abdeckungszeile des
Dossiers `gap_terms`: drei bis acht Suchwörter in der Sprache der gespeicherten Quellen, die ein
Abschnitt enthalten würde, der die Lücke beantwortet. Das Modell füllt sie beim Verfassen und beim
Ändern des Dossiers; die Probe zählt sie als Schlüsselwörter neben denen des Lückentexts. Ältere
Dossiers ohne dieses Feld werden weiterhin geladen und nur über den Text geprobt. Angehängte eigene
Materialien (Quellen ohne Adresse) durchsucht die Probe nicht, weil die Lesesuche sie auslässt.

Ein Treffer widerlegt die Lücke nicht. Eine Begriffssuche über hunderte Abschnitte trifft fast immer
irgendetwas; ein Treffer benennt deshalb einen Abschnitt, der gelesen werden muss. Die Zustände sind
`no_hits` (kein passender Abschnitt), `hits_unread` (Treffer, noch ungelesen), `hits_read_confirmed`
(gelesen, Lücke bleibt bestehen) und `resolved` (in den Quellen beantwortet). Nur `hits_unread`
blockiert: Die Qualitätsprüfung nennt dann den Abschnitt, und `open_questions.md` trägt den Status
hinter jeder offenen Frage. Treffer werden außerdem als erste Lesefenster der zuständigen Teilfrage
vorgemerkt, was keinen zusätzlichen Aufruf kostet.

Im Skriptlauf läuft dieselbe Probe einmal je Lauf bei der Planung über die Unsicherheiten des
Wissensmodells, mit den `gap_terms` des Dossiers, und schreibt `runs/<run_id>/gap_probes.json`. Jede
Zeile nennt in `owner_episodes` die Folgen, deren Quellen einen Treffer enthalten. Vor jedem
Lehrkonzept gehen die ungelesenen Treffer in den Quellen dieser Folge in die bestehende
Ergänzungsrecherche, deren Quellenkontext die Trefferabschnitte enthält. Beantwortet die Ergänzung
die Frage, wird die Zeile `resolved`; nennt sie die Frage in `remaining_gaps`, wird die Zeile
`hits_read_confirmed`, aber nur, wenn alle Trefferabschnitte im Quellenkontext der Ergänzung standen.
`settled_by` hält die Folge fest, die das entschieden hat; eine spätere Folge mit denselben Quellen
gibt dafür keine weitere Ergänzungsrunde aus. Die Skriptprüfung einer Folge wartet genau auf die
Zeilen, die sie selbst hätte lesen müssen: ungelesene Treffer in ihren eigenen Quellen. Treffer, die
in keiner Folge liegen, tragen den Zustand `hits_unowned`; sie blockieren nichts und stehen zur
Nachprüfung in der Laufdatei.

## Grenzen und Wiederaufnahme

Die erste Suche berücksichtigt die Breite der ursprünglichen Leitfragen. Danach prüft eine unabhängige Modellbewertung jede Leitfrage anhand von fünf verbindlichen Merkmalen: vollständige Antwort, erklärter Mechanismus mit Grundlagen und Beispiel, tatsächlich gelesene Belege, passende unabhängige Gegenprüfung sowie Grenzen und begründete Verbindungen. Fehlende Fragen, unlesbare Texte und bloße Inhaltsverzeichnisse bestehen diese Prüfung nicht. Wissenschaftlich offene Fragen dürfen mit belegten konkurrierenden Erklärungen und einer klaren Darstellung des Wissensstands beantwortet werden; fehlende Recherche darf nicht als wissenschaftliche Unsicherheit umgedeutet werden.

Die Fragebearbeitung hat feste Grenzen: höchstens **10 Lese-, Such- oder Antwortentscheidungen je Bearbeitung einer Teilfrage (auch nach Wiederöffnung)**, höchstens **2 zusätzliche Web-Suchen je Teilfrage** und höchstens **2 Wiederöffnungen nach konkreten Einwänden der Gesamtprüfung**. Zwei Schritte ohne neue Treffer, gelesene Abschnitte oder eine bestandene unabhängige Antwortprüfung lösen einmalig eine andere Lesestrategie aus. Identische bereits abgewiesene Antworten werden nicht erneut zur Prüfung geschickt. Bleibt der Fortschritt aus, wird die konkrete Frage mit Begründung blockiert; andere Fragen werden weiter bearbeitet. Ein erneutes Fortsetzen allein setzt diese Grenzen nicht zurück und beginnt keine neue Schleife. Eine blockierte Teilfrage kann ausdrücklich als Lücke akzeptiert werden (Schaltfläche im Studio oder `pla approve <projekt> --accept-gap <task_id> --reason ...`): Der nächste Lauf schließt das Dossier ohne sie ab, führt die Lücke im Qualitätsbericht, in `research/open_questions.md` und in `reports/research_quality.json` (`complete_topic_coverage: false`) und behandelt Prüfeinwände, die nur akzeptierte Lücken betreffen, als dokumentierte Resteinwände statt als neue Recherche. Einwände gegen geprüfte Antworten öffnen diese weiterhin.

Formal ungültige Modellantworten, etwa eine Suche ohne protokollierte Suchanfragen, eine Antwortprüfung mit falscher Kriterienzahl oder ein Plan, der eine Leitfrage auslässt, werden nicht als Zwischenstand gespeichert, sondern mit dem konkreten Beanstandungstext bis zu zweimal erneut angefordert. Abgewiesene Antworten liegen als `*_rejected_NN.json` neben dem gültigen Beleg. Erst die dritte Abweisung blockiert den Lauf; Fortsetzen wiederholt solche Aufrufe nicht. Ein früher gespeicherter Beleg, der die heutige Prüfung nicht besteht, wird ebenso abgelegt und neu angefordert.

Zusätzlich gelten die bestehenden Projektbudgets: standardmäßig **150 Modellaufrufe, 12 Suchrunden und 60 Quellenkandidaten** pro Recherchelauf. Alle Modellentscheidungen und unabhängigen Prüfungen zählen mit; lokale Such- und Leseaktionen selbst benötigen weder Modell noch Netzwerk. Lokales Nachlesen bleibt bei ausgeschöpftem Web- oder Quellenbudget möglich. Grenzen kürzen den vereinbarten Themenumfang nicht automatisch. Dossiers können weiterhin bis zu 120 Befunde enthalten. Aufruf- und Suchrundenlimit lassen sich je Lauf ausdrücklich erhöhen (`pla approve <projekt> --model-calls N --search-rounds M` oder die Schaltflächen im Auftragsstatus). Aufrufe ohne Modellantwort, also Zeitlimit, hängender Stream, Kontingentpause oder Abbruch, werden nicht angerechnet; `budget.json` führt sie unter `refunded`, und die Aufrufnummern bleiben eindeutig. Die Planung erhält die Zahl der Aufgaben, die das genehmigte Limit erfahrungsgemäß trägt (etwa 5 Aufrufe je Teilfrage); ein größerer Plan wird bis zu zweimal mit diesem Hinweis erneut angefordert, danach entscheidet die Mindestprognose. Studio und Fragenbericht zeigen neben dem Mindestbedarf diesen Erfahrungswert.

Die Planung berücksichtigt das ausdrücklich genehmigte Aufruflimit. Nach der Umfangsprüfung zeigen Studio und der Fragenbericht den **Mindestbedarf an weiteren Aufrufen**, die darin enthaltenen Aufrufe für Dossier und Abschlussprüfungen sowie das verfügbare Budget. Fertige Antworten und wiederverwendbare Zwischenstände senken diesen Bedarf. Zusätzliche Suche, Lesen und Korrekturen können mehr benötigen. Reicht das Budget schon für den Mindestbedarf nicht aus, stoppt der Lauf mit `research_budget_insufficient`; Antworten und Themenumfang bleiben erhalten. Optionale Aufrufe dürfen die Abschlussreserve nicht verbrauchen. Eine ausdrücklich genehmigte Erhöhung über die bestehende laufgebundene Freigabe wird beim nächsten Aufruf berücksichtigt.

Das Quellenlimit zählt auch fehlgeschlagene und inhaltlich doppelte Abrufe. Ein dauerhaftes Register reserviert jede neue kanonische Adresse vor dem Abruf. Unterbrochene Downloads setzen ihre Reservierung fort; bereits fertige Downloads werden auch bei ausgeschöpftem Limit aus ihren Belegen wiederhergestellt. Jeder fehlgeschlagene Abruf steht mit Ursache im Abrufbericht: Fehlerklasse des PDF-Lesers, verschlüsselte Datei, Seiten- oder Größenlimit, Seiten ohne Textebene (vermutlich gescannt) oder HTTP-Status, jeweils mit einem festen `code`. Eine Texterkennung (OCR) startet nicht automatisch; eine lesbare Fassung derselben Quelle lässt sich als `seed_urls`-Eintrag nachreichen, was einen neuen Recherchelauf erfordert.

Zusammengeführte Befunde behalten ihre Zuordnung zu den ursprünglichen Teilfragen. Eine wieder geöffnete empirische Frage erlaubt keine Änderung einer weiterhin geschlossenen Definition, auch wenn beide dieselbe übergeordnete Leitfrage beantworten. Geteilte oder nicht eindeutig zuordenbare Befunde bleiben geschützt, solange nicht alle zugehörigen Teilfragen wieder bearbeitet werden. Gezielte Belegkorrekturen dürfen diese Grenze ebenfalls nicht überschreiten.

Die Abschnittssuche trennt heruntergeladene Originaltexte von eigenen Materialien. Sie bevorzugt konkrete Fachbegriffe und erklärende Textstellen gegenüber Autorennamen und Linklisten. Es gibt keine feste Beschränkung auf die ersten zwei Treffer: Das Modell kann weitere Ergebnisse anfordern und gezielt innerhalb eines Dokuments suchen. Ein Leseschritt liefert bis zu 36.000 Zeichen, einschließlich angeforderter Nachbarabschnitte. Nicht mehr passende Abschnitte werden ausdrücklich als noch ausstehend gemeldet. Nur tatsächlich gelesene Textstellen dürfen eine Antwort belegen. Ein Treffer allein schließt keine Frage.

Studio zeigt **geprüfte Teilfragen / insgesamt**, den Bearbeitungsstand, feste Abschlusskriterien, gelesene Abschnitte, fertige Antworten mit Quellenlinks und konkrete Blockaden. Die Gesamtbewertung der ursprünglichen Leitfragen bleibt davon getrennt. Das verhindert, dass ein veralteter Gesamtscore laufende Fortschritte verdeckt. Geöffnete Antworten bleiben beim Aktualisieren aufgeklappt.

Angehaltene ältere Läufe übernehmen beim nächsten **Fortsetzen** die bereits abgerufenen Quellen und den letzten formal gültigen Entwurf aus den gespeicherten Runden. Alte Bewertungen werden als Hinweise übernommen, nicht als automatisch bestandene Einzelantworten. Rohdateien, Abschnittsbezüge und Prüfsummen werden vor der Wiederverwendung kontrolliert. Der Verbrauch im bestehenden Laufbudget bleibt erhalten. Bereits vollständig abgeschlossene Läufe werden nicht nachträglich umgerechnet.

Ein durch ein Abo-Limit pausierter Auftrag wird vom geöffneten Studio-Server zum genannten Reset-Zeitpunkt automatisch fortgesetzt, höchstens dreimal je Auftragskette; die geplante Zeit steht im Auftragsstatus. Andere Unterbrechungen werden nicht automatisch neu gestartet. Beim nächsten Fortsetzen lädt der neue Studio-Worker den aktualisierten Recherchecode; für diese Änderung ist kein Serverneustart nötig. Ein Neuladen der Browserseite lädt die neue Anzeige. Auch im neuen Ablauf werden erfolgreiche Modellantworten und einzelne Downloads gespeichert, damit eine Unterbrechung zwischen Suche, Lesen und Prüfen bereits erledigte Arbeit nicht wiederholt.

Abrufe sind auf 20 MiB und begrenzte Wartezeiten beschränkt; die Namensauflösung wartet höchstens 10 Sekunden. PDF-Texte werden in einem eigenen Prozess mit 120 Sekunden Zeitlimit extrahiert, damit eine fehlerhafte Datei den Auftrag nicht anhalten kann. Importiert werden höchstens 300 PDF-Seiten und eine Million extrahierte Textzeichen je Quelle. PDFs, die nur mit einem Besitzerpasswort gegen Kopieren oder Drucken gesperrt sind, werden gelesen; ein Benutzerpasswort bleibt ein Zugriffsproblem. Bild-PDFs ohne Text, Anmeldeseiten und nicht unterstützte Formate bleiben Zugriffsprobleme. Formeln, Tabellen und Abbildungen können durch automatische Textextraktion unvollständig erfasst werden. [pypdf-Textextraktion und ihre Grenzen](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

Die Quelle bleibt im Rahmen der Importgrenzen vollständig gespeichert. Für jede Teilfrage sieht das Modell gezielt angeforderte Abschnitte; die Gesamtprüfung bekommt die Belegstellen der zusammengeführten Antworten und übernommenen Befunde. Die lokale Suche ist lexikalisch, keine allgemeine Übersetzung oder semantische Suche; englische Fachbegriffe helfen bei englischen Quellen. Kurze direkte Zitate und Paraphrasen werden im Dossier pro Quelle begrenzt.

Gültige abgeschlossene Stufen werden bei Wiederaufnahme übersprungen. Erfolgreiche Einzelabrufe besitzen zusätzliche Prüfsummen-Checkpoints. Bei einer Parseraktualisierung können gespeicherte Rohdateien erneut verarbeitet werden; abhängige Dossier- und Review-Stufen werden dann erneuert. Beschädigte Rohdateien werden erneut abgerufen. Ein Kontingentlimit pausiert den Lauf; ein ausgeschöpftes konfiguriertes Recherchebudget blockiert weitere Modellaufrufe dieses Laufs.

Einzelne Textmodellaufrufe haben standardmäßig **30 Minuten** Zeit (`runtime.text_timeout_seconds: 1800`). Das ist unabhängig vom Budget von 150 Aufrufen. Bestehende explizite Zeitlimits bleiben erhalten. Nach einem Timeout lässt sich dieses Zeitlimit in `project.yaml` erhöhen und derselbe Recherchelauf fortsetzen: Fertige Suche und Quellen bleiben erhalten, nur der unterbrochene Aufruf wird wiederholt; der abgebrochene Aufruf wird nicht angerechnet. Die ursprüngliche Themen- und Modellauswahl bleibt verbindlich. Codex speichert die tatsächliche Zeitgrenze in den Aufrufmetadaten beziehungsweise im Fehlerbericht. Streamende Aufrufe (Codex App Server, Claude Code) gelten zusätzlich nach **10 Minuten ohne jede Ausgabe** als hängend: Sie werden beendet, nicht angerechnet und einmal automatisch wiederholt (`stall_retry.json` im Aufrufordner); erst ein zweiter Stillstand hält den Lauf an. Für Claude wird die Promptgröße vor dem Start gegen das Kontextfenster geprüft (`prompt_too_large`, nicht angerechnet); bei automatischer Abo-Wahl übernimmt Codex solche Aufrufe.

Ein abgeschlossener Recherchelauf bedeutet: Alle vereinbarten Leitfragen erfüllen die Qualitätsmerkmale, ihre Antworten haben gültige Quellenbezüge und die Quellenprüfung enthält keine verbleibenden Einwände. Das ist eine automatisierte Abnahme des vereinbarten Rechercheumfangs, keine Behauptung endgültigen Wissens über ein Fachgebiet. Prüfdetails stehen in `research/quality.md` und `reports/research_quality.json`; während der Recherche zeigt Studio den aktuellen Stand je Leitfrage. `complete_topic_coverage` bezieht sich ausdrücklich auf `coverage_scope: agreed_brief`.

Die Planung übernimmt ausschließlich die abschließend geprüften Artefakte aus `complete_research/`. Ältere Dossiers ohne diese Qualitätsstufe müssen neu recherchiert werden. Beginnt eine neue Recherche, wird das alte Inhaltsverzeichnis aus der aktuellen Auswahl genommen; sein Lauf und die bisherigen Dateien bleiben erhalten. Solange die neue Recherche offen ist, kann ein älteres Dossier nicht ersatzweise eine neue Planung freigeben. [Skripte erstellen und prüfen](scripts.md).
