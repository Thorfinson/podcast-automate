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

Dieser neue Lauf übernimmt eine geprüfte Kopie der Quellen und dokumentiert den ursprünglichen Suchlauf. Er sucht nicht erneut im Web und behält das ursprüngliche Abrufdatum. Geänderte Forschungsfragen, beschädigte Dateien, veränderte lokale Quellen oder ein unpassender Parserstand verhindern die Übernahme. Dossier und Review werden neu erstellt. Eine aktuelle Nachrecherche startet mit `research` ohne diese Option.

## Erklärweise

Der Standard setzt keine fachlichen oder mathematischen Vorkenntnisse voraus. Jede wichtige Idee wird in Alltagssprache aufgebaut: vertraute Situation, klares mentales Bild, einzelne Schritte und deren Wirkung. Nötige Begriffe folgen erst nach der Erklärung. Der Auftrag steht dauerhaft in `audience_level`, `prior_knowledge` und `depth_request` des Projekts; die Dossier- und Review-Anweisungen wenden ihn an.

Eine Formel lediglich in Wörter umzuschreiben erfüllt diese Vorgabe nicht. Der Text erklärt, was ein Schritt tut, warum er hilft und wo er scheitern kann. Bei universitärem Anspruch hält das Dossier auch die begründenden Zwischenschritte fest, die Grundlagen und fortgeschrittene Mechanismen verbinden. Nötige Konzepte wie Gradient und Normierung werden erklärt, nicht pauschal ausgeschlossen. Eine intuitive Herleitung ersetzt keinen formalen Beweis; tatsächlich fehlende Belege bleiben als Lücke sichtbar.

Ein erfundenes Bild steht getrennt vom belegten Befund in `illustration`. Dazu gehört immer `illustration_limit`: Wo hilft der Vergleich nicht mehr? Im lesbaren Dossier erscheinen beide als „Bild zum Mitdenken“ und „Grenze des Bildes“. Der Review prüft auch verständliche Sprache und irreführende Vergleiche. Originaltitel und kurze Belegzitate bleiben im Quellenteil erhalten; sie sind kein gesprochener Podcasttext.

## Verarbeitung

1. **Discovery:** Codex leitet Fragen und Suchanfragen ab und sucht live nach zugänglichen Primärquellen. Die Anwendung prüft tatsächliche Suchereignisse im CLI-Protokoll; eine bloße Modellbehauptung reicht nicht.
2. **Retrieval:** Die Anwendung lädt HTML, PDF oder Text selbst, speichert die Rohdateien und extrahiert Abschnitte mit stabilen IDs. PDFs behalten Seitenreferenzen. Doppelte URLs und identische Texte werden erkannt; Zugriffsfehler bleiben im Bericht.
3. **Dossier:** Codex erhält ausgewählte, tatsächlich extrahierte Abschnitte. Jeder Befund verweist auf `source_id#section_id` und einen kurzen wörtlichen Anker. Unbekannte Abschnitts-IDs, erfundene Zitate und unvollständige Fragezuordnungen verhindern die Übernahme. Ein begrenzter Reparaturversuch ist möglich.
4. **Review:** Ein separater Modellaufruf prüft Befunde, Verständlichkeit und Frageabdeckung gegen dieselben Quellenabschnitte. Bis zu drei Überarbeitungen mit erneuter Prüfung sind möglich, innerhalb des gesamten Aufrufbudgets. Entwurf, Einwände und verbrauchte Versuche bleiben bei Unterbrechung erhalten. Verbleibende Einwände blockieren den Export. Die Prüfung behauptet keine menschliche Abnahme oder vollständige Themenabdeckung.
5. **Export:** Das geprüfte Dossier und seine Begleitdateien werden als aktueller Projektstand geschrieben. Forschungsartefakte und Rohquellen bleiben lokal von Git ausgeschlossen.

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
| `reports/research_quality.json` | Prüfstatus, Grenzen, Quellzahl und verbrauchtes Laufbudget |
| `runs/<run_id>/` | Manifest, Zwischenstände, Suchereignisse, Modellmetadaten und Reparaturbefunde |

Ein Quellenkandidat ist noch kein Beleg. Nur eingelesene Abschnitte, die dem schreibenden Modell tatsächlich vorlagen, dürfen zitiert werden. Publikationsangaben können teilweise aus Suchergebnissen stammen und sind als noch zu prüfende Metadaten gekennzeichnet.

## Grenzen und Wiederaufnahme

Der erste Recherchepass wählt höchstens acht gefundene Quellen; `research_limits.sources` kann dieses Maximum weiter reduzieren und begrenzt insgesamt die unterschiedlichen Quellkandidaten einschließlich expliziter lokaler Dateien und Seed-URLs. `model_calls` zählt jeden gestarteten strukturierten Modellaufruf einschließlich Reparaturen und Wiederaufnahme. `search_rounds` zählt Discovery-Aufrufe; ein solcher Aufruf kann mehrere native Suchanfragen enthalten. Die tatsächlichen Suchereignisse werden separat protokolliert. Es gibt noch keine automatisch nachrecherchierende Schleife für jede erkannte Lücke.

Abrufe sind auf 20 MiB und begrenzte Wartezeiten beschränkt. Importiert werden höchstens 300 PDF-Seiten und eine Million extrahierte Textzeichen je Quelle. Bild-PDFs ohne Text, Anmeldeseiten und nicht unterstützte Formate bleiben Zugriffsprobleme. Formeln, Tabellen und Abbildungen können durch automatische Textextraktion unvollständig erfasst werden. [pypdf-Textextraktion und ihre Grenzen](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

Für das Modell werden ungefähr 150.000 Textzeichen auf die vorhandenen Quellen verteilt und relevante Abschnitte anhand der Suchbegriffe ausgewählt. Die genaue Auswahl steht in `runs/<run_id>/source_context.json`. Die Quellen bleiben vollständig im Rahmen der Importgrenzen gespeichert; die Modellbewertung ist auf die vorgelegten Abschnitte begrenzt. Kurze direkte Zitate und Paraphrasen werden pro Quelle begrenzt.

Gültige abgeschlossene Stufen werden bei Wiederaufnahme übersprungen. Erfolgreiche Einzelabrufe besitzen zusätzliche Prüfsummen-Checkpoints. Bei einer Parseraktualisierung können gespeicherte Rohdateien erneut verarbeitet werden; abhängige Dossier- und Review-Stufen werden dann erneuert. Beschädigte Rohdateien werden erneut abgerufen. Ein Kontingentlimit pausiert den Lauf; ein ausgeschöpftes konfiguriertes Recherchebudget blockiert weitere Modellaufrufe dieses Laufs.

Ein abgeschlossener Recherchelauf bedeutet: Die definierten Stufen sind durchlaufen und die Referenzprüfung sowie der Modellreview haben keine verbleibenden blockierenden Befunde. Er bedeutet nicht, dass das Thema vollständig erforscht oder eine fertige Folge fachlich abgenommen ist. `pla script` verarbeitet das Dossier anschließend zu einem kompakten Wissensmodell, einem Serienentwurf und Dialogskripten zur Leseprüfung. [Skripte erstellen und prüfen](scripts.md).
