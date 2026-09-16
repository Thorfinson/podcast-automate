# Themenrecherche und Dossier

`pla research <project-dir>` setzt den Weg **Thema → Live-Suche → abgerufene Quellen → belegtes Dossier** um. Das Thema steht in `project.yaml`; vorbereitete Quelldateien sind keine Voraussetzung. Optionale `seed_urls`, `seed_people`, `focus_questions` und `local_sources` ergänzen den Auftrag. Relative lokale Quellpfade beziehen sich auf den Projektordner.

## Starten und fortsetzen

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
3. **Dossier:** Codex erhält ausgewählte, tatsächlich extrahierte Abschnitte. Jeder Befund verweist auf `source_id#section_id` und einen kurzen wörtlichen Anker. Unbekannte Abschnitts-IDs, erfundene Zitate und unvollständige Fragezuordnungen verhindern die Übernahme. Ein begrenzter Reparaturversuch ist möglich.
4. **Review:** Ein separater Modellaufruf prüft Befunde, Verständlichkeit und Frageabdeckung gegen dieselben Quellenabschnitte. Er unterscheidet fehlende Belege von Textkorrekturen: Quellenlücken führen direkt zur gezielten Nachrecherche, bevor das Dossier erneut geschrieben wird. Nur wenn die vorhandenen Abschnitte bereits ausreichen, sind bis zu drei Textkorrekturen mit erneuter Prüfung möglich. Bei gemischten Einwänden kommen zuerst neue Belege; die Textkorrekturen werden anschließend mit erledigt. Entwurf, Einwände und verbrauchte Versuche bleiben bei Unterbrechung erhalten. Verbleibende Einwände blockieren den Export. Die Prüfung behauptet keine menschliche Abnahme oder vollständige Themenabdeckung.
5. **Vollständigkeit und Nachrecherche:** Eine separate Prüfung gleicht das Dossier mit jeder ursprünglichen Leitfrage ab. Fehlende Belege und Erklärungen lösen weitere Suche, Abruf, Ergänzung und erneute Prüfung aus. Erst wenn alle Qualitätsmerkmale erfüllt sind, darf der Lauf weitergehen.
6. **Export:** Das abschließend geprüfte Dossier und seine Begleitdateien werden als aktueller Projektstand geschrieben. Forschungsartefakte und Rohquellen bleiben lokal von Git ausgeschlossen.

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
| `research/quality.md` | Qualitätsmerkmale und begründete Bewertung jeder ursprünglichen Leitfrage |
| `reports/research_quality.json` | Prüfstatus, Grenzen, Quellzahl und verbrauchtes Laufbudget |
| `runs/<run_id>/` | Manifest, Zwischenstände, Suchereignisse, Modellmetadaten und Reparaturbefunde |

Ein Quellenkandidat ist noch kein Beleg. Nur eingelesene Abschnitte, die dem schreibenden Modell tatsächlich vorlagen, dürfen zitiert werden. Publikationsangaben können teilweise aus Suchergebnissen stammen und sind als noch zu prüfende Metadaten gekennzeichnet.

## Grenzen und Wiederaufnahme

Die erste Suche berücksichtigt die Breite der ursprünglichen Leitfragen. Danach prüft eine unabhängige Modellbewertung jede Leitfrage anhand von fünf verbindlichen Merkmalen: vollständige Antwort, erklärter Mechanismus mit Grundlagen und Beispiel, tatsächlich gelesene Belege, passende unabhängige Gegenprüfung sowie Grenzen und begründete Verbindungen. Fehlende Fragen, unlesbare Texte und bloße Inhaltsverzeichnisse bestehen diese Prüfung nicht. Wissenschaftlich offene Fragen dürfen mit belegten konkurrierenden Erklärungen und einer klaren Darstellung des Wissensstands beantwortet werden; fehlende Recherche darf nicht als wissenschaftliche Unsicherheit umgedeutet werden.

Bei Lücken folgen automatisch gezielte Suche, Quellenabruf, Dossierergänzung, Quellenprüfung und eine erneute Bewertung aller ursprünglichen Leitfragen. Jede Runde und jeder Abruf bleiben gespeichert. Standard sind **150 Modellaufrufe, 12 Suchrunden und 60 Quellenkandidaten** pro Recherchelauf. Bestehende explizite Limits bleiben erhalten. Limits stoppen den Lauf mit sichtbaren offenen Fragen; sie führen nicht zu einer automatischen Kürzung des Themenumfangs. Eine Suchrunde kann mehrere native Suchanfragen enthalten. Dossiers können bis zu 120 Befunde enthalten; der Inhalt bestimmt die benötigte Zahl.

Findet die Quellenprüfung konkrete Evidenzlücken, durchsucht der Ablauf zuerst die vollständig gespeicherten Textabschnitte, gezielt für jede offene Frage. Diese lokale Suche benötigt weder einen Modellaufruf noch einen Download. Sie bewertet Suchbegriffe je Frage, berücksichtigt ähnliche Wortformen technischer Begriffe und nimmt benachbarte Abschnitte als Kontext hinzu. Die zusätzlichen Textstellen sind auf 40.000 Zeichen je Schritt begrenzt. Ein Treffer schließt noch keine Frage: Die gezielte Ergänzung, Quellenprüfung und Bewertung aller ursprünglichen Leitfragen bleiben erforderlich. Reichen die gelesenen Texte nicht aus, folgt neue Webrecherche. Lokales Nachlesen verbraucht keine Web-Suchrunde und bleibt auch bei ausgeschöpftem Quellenlimit möglich; Modellaufrufe zählen weiterhin zum Budget.

Ergänzungen und Korrekturen werden als Änderungen einzelner Befunde gespeichert. Unbeteiligte Befunde, ihre IDs, Thema und Umfang bleiben erhalten. Formale Belegfehler lösen ebenfalls eine gezielte Korrektur aus; bei gemeinsamen Zitatgrenzen werden die betroffenen Befunde derselben Quelle zusammen berücksichtigt. Danach wird das zusammengesetzte Dossier vollständig auf Quellenbezüge und weiterhin anhand aller vereinbarten Qualitätsmerkmale geprüft. Neue Quellen-Einwände ersetzen überholte Einwände; offene Leitfragen und weiterhin offene Dossierfragen bleiben verbindlich. Eine erfolglose Suche ohne neue relevante Abschnitte löst keine erneute Dossier-Umschreibung aus.

Unter `completeness/round_*/local_lookup.json` stehen Suchfragen und ausgewählte gespeicherte Textstellen, unter `retrieved_lookup.json` die Auswahl aus zusätzlichen Quellen. `*_patch.json` und `*_patch_applied.json` dokumentieren Änderungen, Ausgangshash und zusammengesetztes Ergebnis. Erfolgreiche Modellantworten werden beim Fortsetzen wiederverwendet. Alte Runden mit gespeichertem Dossier werden im bisherigen Format wiederaufgenommen; ab der nächsten unbegonnenen Runde greift das neue Verfahren. Wurde nach Suche/Download, aber vor einem gespeicherten Dossier angehalten, werden Suche und Quellen übernommen und direkt gezielte Änderungen erstellt. Es startet einen angehaltenen Auftrag nicht automatisch. Beim nächsten **Fortsetzen** lädt der neue Studio-Worker den aktualisierten Recherchecode; für diese Änderung ist kein Serverneustart nötig.

Abrufe sind auf 20 MiB und begrenzte Wartezeiten beschränkt. Importiert werden höchstens 300 PDF-Seiten und eine Million extrahierte Textzeichen je Quelle. Bild-PDFs ohne Text, Anmeldeseiten und nicht unterstützte Formate bleiben Zugriffsprobleme. Formeln, Tabellen und Abbildungen können durch automatische Textextraktion unvollständig erfasst werden. [pypdf-Textextraktion und ihre Grenzen](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

Für das erste Dossier werden ungefähr 150.000 Textzeichen auf die vorhandenen Quellen verteilt und relevante Abschnitte anhand der Suchbegriffe ausgewählt. Die erste Auswahl steht in `runs/<run_id>/source_context.json`; spätere Runden ergänzen gezielt bislang nicht vorgelegte Textstellen und behalten bereits gelesene Belege. Die Quellen bleiben vollständig im Rahmen der Importgrenzen gespeichert; die Modellbewertung ist auf die vorgelegten Abschnitte begrenzt. Die lokale Suche ist lexikalisch, keine allgemeine Übersetzung oder semantische Suche; passende englische Suchbegriffe helfen bei englischen Quellen. Kurze direkte Zitate und Paraphrasen werden pro Quelle begrenzt.

Gültige abgeschlossene Stufen werden bei Wiederaufnahme übersprungen. Erfolgreiche Einzelabrufe besitzen zusätzliche Prüfsummen-Checkpoints. Bei einer Parseraktualisierung können gespeicherte Rohdateien erneut verarbeitet werden; abhängige Dossier- und Review-Stufen werden dann erneuert. Beschädigte Rohdateien werden erneut abgerufen. Ein Kontingentlimit pausiert den Lauf; ein ausgeschöpftes konfiguriertes Recherchebudget blockiert weitere Modellaufrufe dieses Laufs.

Einzelne Textmodellaufrufe haben standardmäßig **30 Minuten** Zeit (`runtime.text_timeout_seconds: 1800`). Das ist unabhängig vom Budget von 150 Aufrufen. Bestehende explizite Zeitlimits bleiben erhalten. Nach einem Timeout lässt sich dieses Zeitlimit in `project.yaml` erhöhen und derselbe Recherchelauf fortsetzen: Fertige Suche und Quellen bleiben erhalten, nur der unterbrochene Aufruf wird wiederholt und erneut gezählt. Die ursprüngliche Themen- und Modellauswahl bleibt verbindlich. Codex speichert die tatsächliche Zeitgrenze in den Aufrufmetadaten beziehungsweise im Fehlerbericht.

Ein abgeschlossener Recherchelauf bedeutet: Alle vereinbarten Leitfragen erfüllen die Qualitätsmerkmale, ihre Antworten haben gültige Quellenbezüge und die Quellenprüfung enthält keine verbleibenden Einwände. Das ist eine automatisierte Abnahme des vereinbarten Rechercheumfangs, keine Behauptung endgültigen Wissens über ein Fachgebiet. Prüfdetails stehen in `research/quality.md` und `reports/research_quality.json`; während der Recherche zeigt Studio den aktuellen Stand je Leitfrage. `complete_topic_coverage` bezieht sich ausdrücklich auf `coverage_scope: agreed_brief`.

Die Planung übernimmt ausschließlich die abschließend geprüften Artefakte aus `complete_research/`. Ältere Dossiers ohne diese Qualitätsstufe müssen neu recherchiert werden. Beginnt eine neue Recherche, wird das alte Inhaltsverzeichnis aus der aktuellen Auswahl genommen; sein Lauf und die bisherigen Dateien bleiben erhalten. Solange die neue Recherche offen ist, kann ein älteres Dossier nicht ersatzweise eine neue Planung freigeben. [Skripte erstellen und prüfen](scripts.md).
