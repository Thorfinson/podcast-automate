# SPEC: Podcast Automate – Deep-Dive-Serien

## 1. Produktentscheidung

Stand der Priorisierung: 2026-09-10. Der Hauptfall ist eine persönliche Deep-Dive-Podcastserie zu einem vorgegebenen Thema. Umfang und gewünschte inhaltliche Tiefe bestimmen die Folgenzahl und Gesamtdauer. Komplexe Themen bekommen die zusätzlichen Folgen, die ihre gründliche Erklärung benötigt. Es gibt keine feste Gesamtlänge oder Folgenzahl. Eine einzelne Folge dauert höchstens 30 Minuten.

Der Nutzer gibt ein Thema oder eine zentrale Frage vor. Personen, Thesen, Vorträge, Papers, Links und eigene Dateien sind optionale Ausgangspunkte. Die Quellenrecherche gehört zum MVP; ein bereits gefüllter Quellenordner ist keine Voraussetzung.

Der Standard setzt keine fachlichen oder mathematischen Vorkenntnisse voraus und erklärt auch anspruchsvolle Zusammenhänge in Alltagssprache. Klare mentale Bilder, konkrete Beispiele und kleine Erklärschritte tragen die gewünschte Tiefe. Vorwissen und Detailtiefe können im Themenauftrag angepasst werden. Zusammenhänge dürfen längere Erklärungen benötigen; die Gesprächsform erzwingt keine kurzen Sprecherantworten.

Der MVP startet als CLI mit lokalen Projektdateien auf Windows 11. Der Zielrechner besitzt eine AMD Radeon RX 9070 XT. Textverarbeitung nutzt zunächst das vorhandene ChatGPT-/Codex-Abo über die offizielle CLI; lokale Sprachausgabe und automatische Audiomontage gehören zum MVP. Zusätzliche bezahlte APIs und manueller Audioschnitt sind keine Voraussetzung. Diese Spezifikation beschreibt den vollständigen Zielumfang. Version 0.1 implementiert Projektverwaltung, technische Text-/Audio-Proben und einen begrenzten Recherchepass bis zum belegten Dossier; der aktuelle Stand steht in der [Windows-Anleitung](docs/windows-quickstart.md) und der [Rechercheanleitung](docs/research.md).

### Hauptfälle

- Maschinenlernen, etwa die Frage nach energiebasierten Modellen anhand konkret recherchierter Arbeiten von Yann LeCun und Alfredo Canziani: Grundlagen, Funktionsweise, Beispiele, Vergleich, Beleglage und offene Fragen.
- Blutwerte: Begriffe, Zusammenhänge und Grenzen der Interpretation; konkrete Aussagen einer vom Nutzer genannten Person werden mit weiteren fachlichen Quellen eingeordnet. Die Nutzerangabe „Timo Osterhaus“ ist vor einer Quellenzuordnung anhand eines konkreten Links oder Werkes zu verifizieren.

Die Beispiele legen keine fachlichen Ergebnisse fest. Eine genannte Person ist ein Rechercheeinstieg. Ihre Aussagen werden als solche belegt und von anderen Positionen und übergreifender Evidenz unterschieden.

### Zurückgestellt

Tutor-Modus, Quiz, Karteikarten, Prüfungsmodus, adaptive Lerndiagnostik und Spaced Repetition sind außerhalb des MVP. Sie erzeugen keine Pflichtfelder, Prompt-Stufen, Exporte oder Abnahmekriterien für Deep-Dive-Serien. Weitere spätere Optionen sind Web-App, Teamfunktionen und automatische Veröffentlichung.

## 2. MVP-Scope und Artefakte

Der vollständige MVP führt vom Themenauftrag bis zur hörbaren Serie. Audio-Export ist eine MVP-Funktion; jeder Lauf kann vor der Audioerzeugung mit prüfbaren Skripten enden.

Alle Pfade sind relativ zum Projektordner:

| Pflichtartefakt | Zweck |
| --- | --- |
| `project.yaml` | Themenauftrag, Vorwissen, gewünschte Tiefe, Schwerpunkte und Sprache |
| `research/research_plan.yaml` | Teilfragen, Suchstrategie, Abdeckung und Recherchegrenzen |
| `research/source_candidates.yaml` | Gefundene Quellen, Auswahlgründe und Zugriffsprobleme |
| `models/source_index.yaml` | Eingelesene Quellen, Abschnitte, Metadaten, Rechte und Hashes |
| `models/knowledge_model.yaml` | Begriffe, Claims, Evidenz, Abhängigkeiten, Beispiele und Unsicherheiten |
| `research/research_briefing.md` | Quellenübergreifende Synthese |
| `research/argument_map.md` | Argumente und ihre Verbindungen |
| `research/open_questions.md` | Offene Fragen, Widersprüche und Recherchelücken |
| `models/series_plan.yaml` | Inhaltlich begründete Folgenzahl, Reihenfolge, Themenabdeckung, Gesamtbogen und Laufzeitschätzungen |
| `episodes/<episode_id>/episode_plan.yaml` | Szenen, Erklärziele, benötigte Claims und Übergänge |
| `episodes/<episode_id>/script.yaml` | Kanonisches Skript mit gesprochenem Text, Regie und Wissensmodell-Referenzen |
| `episodes/<episode_id>/script.md` | Aus dem kanonischen Skript erzeugte Lesefassung |
| `episodes/<episode_id>/show_notes.md` | Quellen, Kapitelübersicht und ergänzende Hinweise |
| `reports/quality_report.yaml` | Befunde je Folge und für die gesamte Serie |
| `runs/<run_id>/run_manifest.yaml` | Versionen, Eingaben, Ausgaben, Fortschritt, Freigabestand und verfügbare Verbrauchsdaten |

Nach Audio-Freigabe entstehen zusätzlich für jede ausgewählte Folge:

- `exports/<episode_id>/audio.mp3`,
- `exports/<episode_id>/chapters.json`,
- `exports/<episode_id>/transcript.md`,
- `exports/<episode_id>/show_notes.md`.

Während der Audioerzeugung hält `audio/<episode_id>/timeline.json` die tatsächlich gemessenen Segmentzeiten und ihre Zuordnung zu Skript und Kapiteln fest. Wiederverwendbare Audiosegmente liegen in `cache/audio/`. Laufdaten, Modellgewichte und Zugangsdaten gehören nicht ins Git-Repository.

Rohquellen liegen in `sources/raw/`, bereinigte Abschnitte in `sources/processed/`. Run-Verzeichnisse halten Eingabe- und Ausgabestände sowie Logs fest. Der Claim Graph ist Bestandteil des `KnowledgeModel`; eine zusätzliche Datei mit konkurrierendem Datenstand ist nicht erforderlich.

## 3. Architektur und Arbeitsablauf

Die Pipeline hat explizite, versionierte Stufen. Das `KnowledgeModel` ist die gemeinsame inhaltliche Grundlage der gesamten Serie.

| Stufe | Aufgabe | Hauptausgabe |
| --- | --- | --- |
| Themenplanung | Leitfrage eingrenzen, Vorwissen und gewünschte Tiefe festhalten | Themenauftrag und Rechercheplan |
| Recherche | Quellen finden, Personen und Werke zuordnen, Lücken erkennen | Quellenkandidaten |
| Ingestion | Quellen normalisieren, segmentieren, deduplizieren und referenzierbar machen | Quellenindex |
| Analyse und Synthese | Aussagen, Belege, Voraussetzungen und Gegenpositionen verbinden | Wissensmodell und Briefing |
| Serienplanung | Aus Verständnisweg und Themenabdeckung Folgen ableiten und ihre Laufzeit schätzen | Serienplan |
| Folgenplanung und Skript | Jede Folge mit passendem Kontext gründlich ausarbeiten | Episodenplan, Skript, Show Notes |
| Qualitätsprüfung | Einzelne Folgen und ihre Zusammenhänge prüfen | Qualitätsbericht |
| Audio und Export | Freigegebene Skripte sprechen, prüfen und paketieren | MP3-Folgen und Begleitdateien |

Eine lange Serie wird nicht in einem einzigen vollständigen Skriptaufruf angefordert. Jede Folge erhält ihre relevanten Quellenabschnitte, den Serienplan, benötigte Wissensmodell-Einträge und einen Überblick über bereits Erklärtes und noch offene Fragen.

Ergänzende Recherche ist möglich, wenn Planung oder Schreiben eine konkrete Lücke aufdecken. Neue fachliche Inhalte werden zuerst im Wissensmodell verankert; betroffene Pläne, Skripte und Qualitätsberichte werden anschließend erneut geprüft. Fehlgeschlagene Stufen bleiben wiederaufnehmbar.

Deterministisch sind Ablauf, Schema-Prüfungen und die Wiederverwendung gespeicherter Ergebnisse. Neue Modellaufrufe sind nicht als wortgleich reproduzierbar zugesichert. Ein gespeicherter Lauf muss aus seinen eingefrorenen Eingaben und Ergebnissen nachvollziehbar und erneut exportierbar sein.

## 4. CLI-Entscheidung

Bereits implementiert sind `init`, `doctor`, `status`, `resume`, `schemas`, `text-probe`, `audio-probe`, `research` und `script`. Die beiden Proben schreiben Ergebnisse nach `probes/`. `research` führt Live-Suche, Quellenimport, Dossiererstellung und Quellenreview aus. `script` erstellt daraus ein kompaktes Wissensmodell, einen Serienentwurf und geprüfte Dialogskripte zur Leseprüfung. `script --episode ep_001` zieht die erste Folge vor; ohne Auswahl werden alle geplanten Skripte geschrieben. Audio wird dabei nicht erzeugt. [Skriptworkflow](docs/scripts.md). Die folgende Befehlsfolge beschreibt darüber hinaus die vollständige Zielpipeline; nicht jeder aufgeführte Befehl ist bereits einzeln implementiert:

```bash
pla doctor
pla init <project-dir> --topic "Thema oder Frage"
pla research <project-dir>
pla ingest <project-dir>
pla model <project-dir>
pla plan <project-dir>
pla script <project-dir> [--episode ep_001]
pla check <project-dir>
pla render <project-dir> [--episode ep_001] --approve-audio
pla export <project-dir>
pla run <project-dir> [--approve-audio]
pla status <project-dir>
pla resume <project-dir> [--approve-audio]
```

Ohne zusätzliche Zeitvorgabe wird die Serienlänge aus dem Inhalt abgeleitet. Nur wenn der Nutzer ausdrücklich eine Gesamtdauer wünscht, kann `pla init` optional `--total-minutes <minutes>` als Planungswunsch übernehmen; der Parameter hat keinen Standardwert.

`pla doctor` prüft lokale Voraussetzungen, CLI-Anmeldung und Verfügbarkeit des Audio-Backends. `pla run` führt ohne Audio-Freigabe die Stufen von Recherche bis Qualitätsbericht aus. Mit `--approve-audio` folgen automatisch Audioerzeugung, Montage, Prüfung und Export. Ohne `--episode` bearbeiten `script` und `render` alle geplanten Folgen. Einzelne Skripte können zur redaktionellen Prüfung vorgezogen werden.

`pla render` benötigt sowohl `--approve-audio` als auch einen aktuellen Qualitätsbericht ohne blockierende Befunde für die ausgewählten Folgen und den Serienplan. Ein bestandener Bericht ersetzt keine Audio-Freigabe. `pla export` erzeugt keine neue Audioausgabe und veröffentlicht nichts automatisch.

Die Freigabe eines Gesamtlaufs umfasst dessen automatisch geprüfte Skripte und begrenzte Reparaturen. Vor jedem Rendern werden die aktuellen Qualitätsprüfungen und die freigegebenen Input-Hashes protokolliert. Automatische Textkorrekturen innerhalb dieses Laufs benötigen erneute bestandene Checks. Eine manuelle Änderung an Eingaben oder Produktionskonfiguration erfordert eine erneute Audio-Freigabe; eine bloße Wiederaufnahme eines unveränderten Laufs übernimmt dessen Freigabe.

`pla status` zeigt Fortschritt, fertige Folgen und konkrete Pausierungs- oder Fehlergründe. `pla resume` setzt den letzten unterbrochenen Lauf mit unveränderten gültigen Ergebnissen fort. Ein ausgeschöpftes Abo-Kontingent führt zu `waiting_for_quota`, nicht zu unvollständigen Endergebnissen oder einem automatischen Wechsel auf bezahlte APIs.

## 5. Datenverträge

Die vollständigen Verträge werden schrittweise als validierbare Schemas umgesetzt. Version 0.1 exportiert über `pla schemas` dreizehn Verträge: TopicBrief, EpisodeScript, TextProbeOutput, RunManifest, ResearchDiscovery, SourceDocument, SourceIndex, ResearchDossier, DossierReview, KnowledgeModel, SeriesPlan, EpisodePlan und ScriptReview. Das kompakte Wissensmodell übernimmt die belegten Befunde unverändert und referenziert Begriffe, Mechanismen, Beispiele und Grenzen über deren IDs. Serienentwurf und Szenen sind validierbar; die in den folgenden Abschnitten beschriebenen Datenmodelle enthalten zusätzlich den noch ausstehenden Zielumfang, etwa differenzierte Konfidenz-/Evidenzbewertungen und die vollständige Serienabdeckung.

### 5.1 TopicBrief (`project.yaml`)

| Feld | Bedeutung |
| --- | --- |
| `topic`, `central_question` | Thema und Leitfrage der Serie |
| `language` | Standardsprache `de-DE` |
| `audience_level`, `prior_knowledge` | Anspruch und bereits bekannte Grundlagen |
| `depth_request` | Gewünschte inhaltliche Tiefe und Erklärschwerpunkte; unabhängig von der Hörzeit |
| `focus_questions`, `excluded_topics` | Gewünschte Schwerpunkte und Grenzen |
| `seed_people`, `seed_urls`, `local_sources` | Optionale Rechercheeinstiege; Personen brauchen eine belegte Quellenzuordnung |
| `target_total_minutes` | Optionaler, ausdrücklich genannter Planungswunsch; standardmäßig nicht gesetzt (`null`), keine implizite Gesamtzeitgrenze |
| `max_episode_minutes` | Harte Obergrenze 30 |
| `research_limits` | Begrenzung für Suchrunden, Quellen und Modellaufrufe pro Arbeitslauf; keine pauschale Grenze für Serienumfang |
| `text_backend`, `tts_backend`, `voice_profile` | Gewählter CLI-Adapter, lokaler TTS-Adapter und beständige Sprecherstimmen; keine Zugangsdaten |
| `style_profile_id` | Standard `de_calm_deep` |
| `export_context` | Standard `private_learning`; öffentlicher Export bleibt außerhalb des MVP |

### 5.2 SourceDocument

Jede Quelle enthält:

- `id`, `type`, `title`, `author`, `published_date`, `imported_at`, `language`, `url`,
- `license_status`: `owned`, `public_domain`, `open_license`, `permission_granted`, `unknown` oder `restricted`,
- `allowed_usage`: `private_learning`, `internal_review`, `publishable_summary`, `publishable_quotes` oder `no_export`,
- `private`: expliziter Ausschluss vom Export,
- `reliability` mit Begründung, Primärquellenbezug, Interessenlage und Zugriffsstatus,
- `text_hash` sowie `sections` mit stabilen IDs, Text und vorhandenen Seiten- oder Zeitmarken,
- `quotes` mit Abschnittsreferenz und `max_export_words`,
- `uncertainties` zur Quelle selbst.

Eine gefundene, aber nicht eingelesene Quelle bleibt ein Quellenkandidat. Titel und Suchausschnitte reichen nicht als Beleg für detaillierte fachliche Aussagen.

### 5.3 KnowledgeModel

| Bestandteil | Erforderlicher Inhalt |
| --- | --- |
| `key_terms` | ID, Definition und Quellenreferenzen |
| `claims` | ID, Aussage, Typ, Konfidenz und Evidence-Einträge mit Quellenabschnitt, Stärke und Begründung |
| `counterpoints` | ID, Gegenargument oder Grenze, Quellenreferenzen und Verbindung zum betroffenen Claim |
| `dependencies` | Welche Begriffe oder Claims vor anderen erklärt sein müssen |
| `mechanisms` | Erklärschritte für einen Zusammenhang mit zugehörigen Claim- und Term-IDs |
| `examples` | Beispiele mit Referenzen; erfundene Veranschaulichungen ausdrücklich als hypothetisch markieren |
| `uncertainties` | ID, offene Frage und Grund, etwa fehlende Daten, widersprüchliche Quellen oder unklare Begriffe |
| `editorial_priorities` | Relevanz für die Leitfrage, notwendige Voraussetzungen und begründet ausgelassene Inhalte |

Quellenreferenzen verwenden `source_id#section_id`. Jeder fachliche Claim hat mindestens einen Evidence-Eintrag. Redaktionelle Schlussfolgerungen werden als solche gekennzeichnet und verweisen auf belegte Ausgangsclaims. Anschauliche Beispiele dürfen keine unbelegten Tatsachen suggerieren.

Tutor-Lernziele, Bloom-Stufen und Quizdaten sind keine erforderlichen Bestandteile.

### 5.4 SeriesPlan

Der Serienplan enthält die Leitfrage, die gewünschte Tiefe, die inhaltlich begründete Folgenzahl, die daraus geschätzte Gesamtdauer, den übergreifenden Erklärbogen und eine geordnete Liste von Folgen. Ein ausdrücklich genannter Zeitwunsch wird separat festgehalten. Je Folge werden mindestens festgehalten:

- `episode_id`, Nummer, Titel, eigene zentrale Frage und Zweck,
- `target_minutes`,
- vorausgesetzte Folgen, Begriffe und Claims,
- neu zu erklärende Begriffe, Claims, Mechanismen und Beispiele,
- behandelte Gegenpositionen und Unsicherheiten,
- bewusst vertagte Fragen mit späterer Zielfolge oder begründetem Ausschluss,
- Anschlussfrage zur nächsten Folge; bei der letzten Folge abschließende Synthese.

Eine Abdeckungsmatrix ordnet jede priorisierte Teilfrage und jeden zentralen Claim einer oder mehreren Folgen zu. Erneute Verwendung wird als notwendige Vertiefung oder kurze Rückschau begründet.

### 5.5 EpisodePlan und Skript

Ein Episodenplan enthält `episode_id`, `mode: deep_dive`, Stilprofil, Zeitbudget, Sprecherrollen und Szenen. Jede Szene hat eine Funktion, Frage, Zielzeit, relevante Wissensmodell-IDs, Erklärschritte und einen Übergang.

Das kanonische `script.yaml` enthält `schema_version`, `episode_id` und geordnete Segmente mit `segment_id`, `scene_id`, `chapter_id`, `speaker_id`, `text`, `knowledge_refs` und `pause_after_ms`. `knowledge_refs` verweist auf stabile IDs des Wissensmodells; dieses führt die Quellenbezüge in der Form `source_id#section_id`. Reine Übergänge können ohne fachliche Referenz auskommen. Quellenbindung und inhaltlicher Review prüfen die Vollständigkeit der Zuordnung.

Die gesprochene Fassung besteht aus vollständigen Erklärungen. Regie und Referenzen werden nicht mitgesprochen. `script.md`, Transkript und Renderaufträge werden aus demselben kanonischen Stand erzeugt. Modellbedingte Unterteilungen langer Sprecherpassagen erfolgen an Satzgrenzen und bleiben auf das ursprüngliche Segment zurückführbar. Die Audio-Zeitleiste enthält die tatsächlich gemessenen Zeiten; redaktionelle Kapitelpositionen sind vor dem Rendern nur geplant.

## 6. Recherche und inhaltliche Tiefe

### Themengeleitete Recherche

1. Aus dem Themenauftrag Teilfragen, notwendige Grundlagen und Suchbegriffe ableiten.
2. Genannte Personen und konkrete Werke zuordnen; bei ungeklärter Identität die Zuschreibung offenlassen.
3. Primärquellen und fachliche Übersichten suchen und auf Aktualität, Relevanz, Methodik und Interessenlage prüfen.
4. Relevante unabhängige Einordnungen, Gegenpositionen und Grenzen suchen.
5. Aussagen quellenübergreifend verbinden und bestehende Widersprüche erklären.
6. Abdeckung und verbleibende Lücken dokumentieren. Bei ausgeschöpftem Budget mit sichtbaren Lücken enden.

Jede priorisierte Teilfrage braucht tragfähiges Material oder einen dokumentierten Befund, warum sie nicht beantwortet werden kann. Wesentliche ungeklärte Grundlagen blockieren die davon abhängigen Folgen. Quellenanzahl allein ist kein Qualitätsnachweis; fehlende unabhängige Bestätigung wird sichtbar gemacht.

### Pflichtniveau

Der MVP muss über Extraktion und Argumentkarte hinaus eine Synthese leisten: Was hängt wie zusammen, worauf beruhen die Aussagen, worin unterscheiden sich Positionen, und was bleibt offen? Diese Anforderung gilt für jede Serie, unabhängig von der Anzahl der Quellen.

Für jede zentrale Erklärfrage müssen im Plan und Skript erkennbar sein:

- präzise Begriffe und benötigte Voraussetzungen,
- eine nachvollziehbare Erklärung des Wie und Warum, soweit die Quellen dies tragen,
- mindestens ein ausführlich durchgearbeitetes Beispiel oder eine Fallanalyse,
- Belege sowie relevante Grenzen, Alternativen oder Unsicherheiten,
- eine Antwort auf die Folgenfrage und deren Beitrag zur Serienfrage.

Die Elemente müssen inhaltlich aufeinander bezogen sein. Ihre bloße Erwähnung erfüllt den Tiefencheck nicht. Ein Gegenargument wird nicht erfunden, wenn die Quellen keines tragen; tatsächliche Grenzen oder offene Fragen werden entsprechend benannt.

### Verständlich ohne Vorwissen

Recherchetexte und spätere Podcastskripte richten sich standardmäßig an neugierige Menschen ohne fachliche oder mathematische Vorkenntnisse. Inhaltliche Tiefe entsteht durch nachvollziehbare Zusammenhänge und Ursachen. Formeln, Abkürzungsketten und unerklärte Fachsprache gehören nicht in den gesprochenen Erklärtext.

Eine zentrale Erklärung beginnt mit einer vertrauten Situation oder einem klaren mentalen Bild. Daran wird Schritt für Schritt gezeigt, was sich verändert und warum. Erst nach der Idee folgt ein Fachbegriff, wenn er zum Verständnis oder Wiedererkennen nützlich ist. Wenige durchgehende Bilder schaffen Orientierung; häufige Wechsel zwischen unverbundenen Metaphern erschweren das Zuhören.

Der Ton bleibt erwachsen und auf Augenhöhe. Fehlendes Spezialwissen ist kein Anlass, Selbstverständliches ausführlich zu erklären. Begriffe werden einmal knapp eingeführt und danach normal verwendet. Wiederholte Definitionen, belehrende Vorreden, das Ankündigen jedes kleinen Schritts und mehrere Zusammenfassungen derselben Idee werden gestrichen. Metaphern sollen einen Zusammenhang erschließen; ihre wichtige Grenze wird einmal dort benannt, wo sie relevant ist.

Metaphern sind ausdrücklich Veranschaulichungen. Die Erklärung benennt ihre Grenze und trennt erfundene Alltagssituationen von belegten Versuchen. Im Dossier werden sie als `illustration` und `illustration_limit` zusammen gespeichert. Der Quellenreview prüft auch, ob das Bild den belegten Zusammenhang korrekt wiedergibt. Im späteren Dialog darf die zweite Stimme dort nachfragen, wo jemand ohne Vorwissen einen Zwischenschritt braucht.

### Fachliche Perspektiven

Eine personenzentrierte Recherche unterscheidet zwischen einer belegten Aussage dieser Person, ihrer Interpretation und dem Befund weiterer Quellen. Fachbegriffe müssen in ihrem jeweiligen Kontext erklärt werden.

Bei Gesundheitsthemen gehören aktuelle fachliche Primärquellen und Leitlinien in die Recherche. Referenzbereiche, Entscheidungsgrenzen und behauptete „Optimalwerte“ werden im Quellenmodell getrennt geführt und nach Herkunft eingeordnet. Persönliche Diagnose oder Behandlung anhand individueller Laborbefunde ist kein Hauptfall dieses MVP.

## 7. Serienplanung und Laufzeit

Die Serie ist das Standardprodukt. Ihre Länge ergibt sich aus Teilfragen, notwendigen Grundlagen, Erklärabhängigkeiten und gewünschter Tiefe. Zuerst wird der inhaltlich nötige Umfang geplant, daraus folgen die Episoden und ihre geschätzte Gesamtdauer. Es gibt weder eine allgemeine Mindest- oder Höchstdauer der Serie noch eine festgelegte Folgenzahl.

- Keine Folge darf 30 Minuten überschreiten.
- Die geschätzte Gesamtdauer ist die Summe der Folgenlaufzeiten und ein Ergebnis der Planung. Sie muss keinen vorgegebenen Stundenbereich treffen.
- Benötigt das Thema mehr Raum, wird die Serie um inhaltlich begründete Folgen erweitert. Die Erweiterung ist kein Qualitätsfehler.
- Eine Serie ist inhaltlich vollständig, wenn ihre priorisierten Fragen in der gewünschten Tiefe beantwortet oder ihre fachlichen Grenzen nachvollziehbar eingeordnet sind.
- Bei unzureichendem Material wird eine kürzere Serie vorgeschlagen oder gezielt nachrecherchiert. Skripte werden nicht künstlich gestreckt.
- Passt eine Folge nicht in ihr Budget, werden sinnvoll abgegrenzte Inhalte in eine weitere Folge verlagert. Belege, Beispiele und Gegenpositionen dürfen dabei nicht pauschal wegfallen.
- Zusätzliche Folgen und Änderungen der geschätzten Gesamtdauer müssen aus dem Serienplan nachvollziehbar sein.
- Ein ausdrücklich genannter Gesamtzeitwunsch wird gegen den nötigen Inhalt abgewogen. Passt die gewünschte Tiefe nicht hinein, weist der Plan die nötige zusätzliche Hörzeit oder konkrete Umfangsänderungen aus; Inhalte werden nicht stillschweigend gekürzt.
- Skriptlaufzeit wird aus gesprochenen Wörtern, konfigurierter Sprechgeschwindigkeit und Pausen geschätzt. Die Rate ist nach dem ersten Audio-Pilot zu kalibrieren.
- Nach dem Rendern zählt die gemessene Audiodauer. Zu lange Folgen werden vor dem finalen Export überarbeitet oder geteilt; die Sprechgeschwindigkeit wird nicht zur Umgehung der Obergrenze erhöht.

### Zusammenhang über mehrere Folgen

Der Plan ordnet die Folgen nach notwendigen Grundlagen und aufeinander aufbauenden Fragen. Geeignete Zwecke sind Grundlagen, Mechanismus, Vertiefung, Gegenposition, Fallstudie, Anwendung und Synthese. Eine Serie muss nicht alle Zwecke als getrennte Folgen verwenden.

Jede Folge benennt knapp, welches Wissen sie voraussetzt, und beantwortet eine eigene Frage substanziell. Kurze Rückschauen sind erlaubt. Bereits erklärte Grundlagen sollen nicht bei jeder Folge wieder den Hauptteil bilden. Die letzte Folge verbindet die Ergebnisse und markiert verbleibende offene Fragen.

## 8. Storytelling und Sprechstil

Das Standardprofil `de_calm_deep` verwendet Deutsch, einen ruhigen, gründlichen Ton, mittleres Sprechtempo, wenig Humor und zwei Hosts.

- Host A entwickelt Erklärungen, führt Beispiele durch und verbindet Befunde.
- Host B fragt nach Mechanismen, prüft Annahmen, bringt Einwände und markiert unklare Begriffe.

Sprecherwechsel folgen dem Gedankengang. Längere zusammenhängende Erklärungen sind ausdrücklich erlaubt. Host B darf nicht überwiegend Zustimmung oder Stichworte liefern. Fragen dienen der Erschließung des Themas; es gibt keine verpflichtenden Quiz- oder Antwortpausen für den Hörer.

Eine Folge führt von einer konkreten Frage oder einem Fall über Kontext, Erklärung, Belege, Komplikation und Vertiefung zur Synthese. Der Einstieg benennt die Folgenfrage innerhalb der ersten 90 Sekunden. Der Schluss greift sie auf. Eine Grundlagenfolge darf einen anderen Spannungsbogen haben als eine Kontroversenfolge; Dramaturgie soll den Inhalt tragen.

## 9. Prompt- und Modellstrategie

Prompts sind getrennte, versionierte Templates mit definierten Eingaben, Ausgaben und Validierung:

1. `plan_research.v1`,
2. `review_source_candidates.v1`,
3. `extract_sources.v1`,
4. `build_knowledge_model.v1`,
5. `synthesize_research.v1`,
6. `plan_series.v1`,
7. `plan_episode.v1`,
8. `write_deep_dive_script.v1`,
9. `review_episode.v1`,
10. `review_series.v1`.

Das erste Textbackend ist Codex CLI mit vorhandener ChatGPT-Abo-Anmeldung. Ein kleiner Adapter kapselt Aufträge, strukturierte Ausgaben, Validierung, verfügbare Nutzungsmetadaten und Fehler. Claude Code kann später denselben Vertrag bedienen. Die Anwendung verwendet die offiziellen CLI-Anmeldungen; sie implementiert keine eigenen Zugriffe mit ausgelesenen Sitzungstokens. Modell und CLI-Version werden pro Lauf festgehalten. Die dokumentierten Grundlagen stehen im [Implementierungsplan](docs/personal-learning-podcast-system-plan.md).

Suche, Abruf und Textextraktion benötigen echte Werkzeuganbindungen. Zunächst werden die Suchwerkzeuge des gewählten CLI-Backends genutzt und ihre Verfügbarkeit durch einen realen Abruf geprüft. Ein Modell darf keine nicht abgerufenen Quellen als gelesene Evidenz ausgeben. Nicht zugängliche Texte bleiben Quellenkandidaten oder dokumentierte Lücken.

Die Abo-Kontingente gelten auch für automatisierte Aufrufe. Fertige Stufenergebnisse werden gespeichert; bei ausgeschöpftem Kontingent wird pausiert. Wiederholungen bei technischen oder Validierungsfehlern sind begrenzt. Ein späterer Anbieterwechsel ist eine ausdrückliche Konfigurationsentscheidung, kein automatischer Ausweg aus einem Limit. Für den MVP werden keine API-Zahlungsdaten vorausgesetzt.

## 10. Qualitätsprüfungen

| Gate | Blockierend | Regel |
| --- | --- | --- |
| `schema_check` | Ja | Artefakte und alle referenzierten IDs sind gültig. |
| `source_check` | Ja | Fachliche Skriptaussagen verweisen auf passende Wissensmodell-Einträge. |
| `evidence_check` | Ja | Claims und fachliche Definitionen sind auf tatsächlich eingelesene Quellen zurückführbar. |
| `factual_review` | Ja bei Befund | Evidenz trägt die Aussage; neue oder überzogene Behauptungen gehen zurück in die Recherche. |
| `research_coverage_check` | Ja bei wesentlichen Lücken | Teilfragen und Grundlagen sind abgedeckt oder mit begründeten Folgen für den Umfang markiert. |
| `depth_check` | Ja | Zentrale Fragen werden anhand von Erklärschritten, ausgearbeiteten Beispielen, Evidenz und Grenzen substanziell beantwortet. |
| `series_planning_check` | Ja | Fragen, Claims und Voraussetzungen sind Folgen zugeordnet; vertagte Kerninhalte gehen nicht verloren. |
| `continuity_check` | Ja bei Verständnisbruch | Reihenfolge und Übergänge funktionieren; Begriffe werden vor ihrer notwendigen Verwendung erklärt. |
| `redundancy_check` | Warnung | Unnötige Wiederholungen innerhalb und zwischen Folgen ersetzen keine Vertiefung. |
| `duration_check` | Ja | Geplante und geschätzte Laufzeit bleiben je Folge bei höchstens 30 Minuten; vor Audio-Export gilt zusätzlich die gemessene Dauer. |
| `rights_check` | Ja | Export berücksichtigt Quellenrechte, Zitatgrenzen, `private` und `no_export`. |
| `audio_readiness_check` | Ja vor Rendern | Sprecher, gesprochener Text, Pausen und Kapitel sind eindeutig. |
| `audio_output_check` | Ja vor finalem Audioexport | Alle Segmente sind vorhanden und technisch gültig; Montage, gemessene Dauer und Kapitel stimmen überein. |

ID- und Schema-Prüfungen sind maschinell deterministisch. Inhaltliche Tiefe, Evidenzpassung und Natürlichkeit benötigen redaktionelle Bewertung; eine Quellen-ID beweist keine sachliche Richtigkeit. Der Bericht trennt automatische Prüfungen, Modellbewertungen und menschliche Befunde.

Ein Bericht speichert die Hashes der geprüften Quellen-, Modell-, Plan- und Skriptstände. Eine Änderung dieser Eingaben macht betroffene Qualitätsfreigaben ungültig. Blockierende Vorabprüfungen verhindern Audio-Rendering; Befunde am erzeugten Audio erlauben gezielte Reparaturversuche, verhindern aber den finalen Export. Interne Artefakte und Fehlerberichte bleiben zur Korrektur verfügbar. Ein Bericht über nur eine Folge darf nicht als Prüfung der gesamten Serie gelten.

## 11. Rechte und Datenschutz

- Importierte Quellen haben zunächst den Rechtezustand `unknown`.
- Quellen mit `unknown` können privat analysiert werden, dürfen aber nicht in öffentlich gedachte Show Notes gelangen.
- `private: true` und `allowed_usage: no_export` schließen Quellen aus Skript- und Audioexporten aus.
- `restricted` erlaubt keine direkten Zitate; Nutzungsstatus und Zitatlimits werden beim Export geprüft.
- Paraphrasen sind der Standard; kurze Zitate bleiben ihrer Quelle zugeordnet.
- Projektdateien werden lokal gespeichert. Logs enthalten keine vollständigen Quellentexte. Personenbezogene Daten werden vor Modellaufrufen in einer Redaction-Stufe behandelt.

Die bestehende Transparenznotiz bleibt Bestandteil der Exporte:

> Dieser Output ist eine quellengebundene Synthese. Er ersetzt keine fachliche, rechtliche, medizinische oder wissenschaftliche Begutachtung. Unsichere oder widersprüchliche Quellenlagen werden markiert.

## 12. Audio und Wiederaufnahme

- Audio wird nur nach expliziter Freigabe und bestandenen blockierenden Prüfungen erzeugt.
- TTS läuft lokal auf dem Windows-Rechner in einer separaten Umgebung. Qwen3-TTS ist der erste Testkandidat; Modellvariante, Stimmen und passende AMD-Laufzeit werden anhand eines frühen Machbarkeitstests festgelegt. Die konkrete Kombination ist bisher nicht erprobt.
- Rendering erfolgt pro Sprechersegment, anschließend werden Segmente automatisch zu Folgen zusammengesetzt. Der Nutzer muss keinen Audioeditor bedienen.
- Die strukturierte Regie steuert Sprecherzuordnung, Pausen und Kapitel. FFmpeg und ffprobe übernehmen Montage und Messung; unbeabsichtigte Randstille darf korrigiert werden, Sprachlaute und geplante Denkpausen müssen erhalten bleiben.
- Der Cache-Key berücksichtigt Provider, Modellrevision, Stimme, gesprochenen Segmenttext, Aussprache- und TTS-Einstellungen einschließlich gegebenenfalls verwendeter Seeds.
- Nur geänderte oder fehlende Segmente werden neu gerendert. Fehlgeschlagene Folgen können einzeln fortgesetzt werden.
- Fehlende oder beschädigte Dateien, leere Ausgabe, auffällige Stille, Pegelfehler und unplausible Dauer führen zu gezielter Prüfung und begrenzten Reparaturversuchen. Bleibt ein Fehler bestehen, wird er mit Segment-ID gemeldet und der betroffene finale Export blockiert.
- Standardformat ist MP3, 44.1 kHz, Stereo und lautheitsnormalisiert auf -16 LUFS.
- Kapitelmarken werden aus der tatsächlichen Audio-Zeitleiste erzeugt. Zu lange Folgen werden automatisch sinnvoll aufgeteilt oder überarbeitet und erneut geprüft.
- Aussprache und Stimmenkonstanz werden im Audiopilot bewertet und über ein zentrales Ausspracheprofil unterstützt. Ergänzende lokale Rücktranskription zur Erkennung von Auslassungen und Wiederholungen wird am Pilot erprobt; sie garantiert keine fehlerfreie Aussprache.
- Vor Produktion werden Umfang und, soweit aus dem Pilot ableitbar, Renderzeit und Speicherbedarf angezeigt. Der Lauf protokolliert tatsächliche Renderdauer, Cache-Nutzung und verfügbare Abo-Verbrauchsdaten. Unbekanntes Restkontingent wird als unbekannt ausgewiesen. Geschätzte API-Dollarwerte einer CLI sind keine tatsächlich berechneten Abo-Kosten.

Musik, aufwendiges Sounddesign und ein dritter Host sind keine Voraussetzungen für den ersten MVP.

## 13. Run Manifest

Das Manifest speichert Run-ID, Erstellungszeit, Pipeline- und Schema-Version, Themenauftrag, Recherchegrenzen, Quellen- und Artefakthashes, Prompt-Versionen, verwendete Modelle und Einstellungen sowie die Ausgabepfade je Folge.

Zusätzlich erfasst es Status und Wiederaufnahmepunkte je Stufe, Folge und Segment, Warnungen und Fehler, verfügbare Verbrauchsdaten, Renderzeiten, Cache-Treffer, Qualitätsstatus, geprüfte Input-Hashes sowie Herkunft und Umfang der Audio-Freigabe für konkrete Skriptstände und Folgen. Statuswerte umfassen `pending`, `running`, `completed`, `waiting_for_quota`, `blocked` und `failed`. Kostenfelder unterscheiden Schätzung und tatsächlich separat berechnete Beträge; sie dürfen aus Abo-Aufrufen keine erfundenen Rechnungsbeträge ableiten. Quellentexte und Modellantworten werden soweit zulässig im Run eingefroren, damit spätere Änderungen an Onlinequellen den ursprünglichen Lauf nicht still verändern.

## 14. Evaluation und Definition of Done

Vor weiteren Ausgabeformaten werden drei Fixture-Projekte angelegt:

1. `fixtures/simple_topic`: kleiner, konsistenter Quellenbestand für schnelle Prüfungen von Ingestion, Referenzen und Export.
2. `fixtures/mechanism_series`: eine Serie mit aufeinander aufbauenden Grundlagen und Mechanismen. Die Planung muss mit zusätzlichem inhaltlichem Bedarf um weitere Folgen wachsen können, ohne an einer festen Gesamtzeit oder Folgenzahl zu scheitern.
3. `fixtures/conflicting_perspectives`: widersprüchliche Positionen, unsichere Evidenz und eine personenzentrierte Ausgangsfrage.

Jedes Fixture erhält erwartete Kernclaims, Erklärschritte, Quellenbezüge, Beispiele, Grenzen und mindestens einen absichtlich problematischen Fall. Das Serienfixture umfasst zudem erwartete Abhängigkeiten, Abdeckung und unerwünschte Wiederholungen. Die Hauptfälle aus Abschnitt 1 sind Kandidaten für spätere fachliche Piloten; konkrete Quellen müssen dafür recherchiert werden.

Der MVP ist fertig, wenn:

- ein Thema ohne vorbereiteten Quellenordner zu einer recherchierten Serie führt,
- alle Pflichtartefakte existieren und ihre strukturierten Daten gegen implementierte Schemas validieren,
- eine vollständige Pilotserie ihren Themenauftrag in der gewünschten Tiefe abdeckt und jede Audiofolge höchstens 30 Minuten dauert,
- zusätzliche inhaltlich nötige Folgen ohne feste Gesamtzeit- oder Folgenbegrenzung geplant und erzeugt werden können,
- geplante und tatsächliche Gesamtdauer sichtbar sind; ein ausdrücklich genannter Zeitwunsch wird separat ausgewiesen,
- Folgen aufeinander aufbauen und zentrale Fragen ausführlich beantworten,
- Aussagen, Gegenpositionen und Unsicherheiten auf überprüfbare Quellen zurückführbar sind,
- blockierende Vorabprüfungen das Rendern verhindern und fehlerhaftes Audio nicht final exportiert wird,
- Skripte vor Audio prüfbar sind und Audio nur nach Freigabe entsteht,
- MP3-Folgen, Kapitel, Transkripte und Show Notes vollständig exportiert werden,
- der vollständige Hauptfall unter Windows 11 mit Abo-Textbackend, lokalem TTS und automatischer Montage ohne manuellen Audioschnitt funktioniert,
- die Fixtures und eine redaktionelle Hörprüfung des Piloten bestanden sind,
- unterbrochene Läufe einschließlich Abo-Pausen ohne vollständige Neuberechnung wiederaufgenommen werden können.

Die vollständige hörbare Serie ist das Abnahmeziel des MVP; ein reiner Skriptprototyp erfüllt es noch nicht. Kriterien für die qualitative Prüfung stehen in [docs/system-quality-assessment.md](docs/system-quality-assessment.md).
