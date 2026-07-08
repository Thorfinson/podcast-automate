# SPEC: Personal Learning Podcast System

## 1. Produktentscheidung

Das Produkt ist ein CLI-first Recherche-zu-Audio-Lernsystem. Es erzeugt aus einem Quellenordner zuerst ein validiertes Wissensmodell und daraus anschließend Skripte und optionale Audio-Exports.

### Zielnutzer im MVP

Der MVP ist für fortgeschrittene Selbstlerner gebaut, die eigene Quellen in ein strukturiertes Lern- und Podcastformat überführen wollen.

Nicht Teil des MVP:

- Team-Kollaboration,
- Web-App,
- mobile App,
- Kursverwaltung,
- Zahlungsmodell,
- öffentlicher Podcast-Hoster,
- automatische Veröffentlichung.

## 2. Verbindlicher MVP-Scope

Der MVP erzeugt genau diese Pflichtartefakte:

1. `knowledge_model.yaml`,
2. `research_briefing.md`,
3. `argument_map.md`,
4. `deep_dive_script.md`,
5. `tutor_script.md`,
6. `quiz_cards.yaml`,
7. `show_notes.md`,
8. `quality_report.yaml`,
9. `run_manifest.yaml`.

Optional erzeugt der MVP nach expliziter Freigabe:

- `audio/deep_dive.mp3`,
- `audio/tutor.mp3`,
- `chapters.json`,
- `transcript.md`.

## 3. Architekturentscheidung

Die Architektur ist eine deterministische Pipeline mit versionierten Zwischenergebnissen.

```text
sources/ -> ingestion -> source_index.yaml
source_index.yaml -> analysis -> knowledge_model.yaml
knowledge_model.yaml -> planning -> episode_plan.yaml
knowledge_model.yaml + episode_plan.yaml -> scripting -> scripts/*.md
scripts/*.md -> quality gates -> quality_report.yaml
scripts/*.md + approved quality_report.yaml -> audio render -> audio/*.mp3
```

Das `KnowledgeModel` ist die Single Source of Truth. Skripte dürfen keine neuen Fakten einführen, die nicht im Wissensmodell als Claim, Uncertainty, Example oder Definition enthalten sind.

## 4. Projektstruktur

Ein Projekt nutzt diese Ordnerstruktur:

```text
project/
  sources/
    raw/
    processed/
  models/
    source_index.yaml
    knowledge_model.yaml
    episode_plan.deep_dive.yaml
    episode_plan.tutor.yaml
  scripts/
    deep_dive_script.md
    tutor_script.md
  reports/
    quality_report.yaml
    ingestion_report.yaml
  exports/
    show_notes.md
    quiz_cards.yaml
    transcript.md
    chapters.json
    audio/
  runs/
    <run_id>/
      run_manifest.yaml
      inputs/
      outputs/
      logs/
```

## 5. CLI-Entscheidung

Der MVP stellt diese Befehle bereit:

```bash
pla init <project-dir>
pla ingest <project-dir>
pla model <project-dir>
pla plan <project-dir> --mode deep_dive|tutor
pla script <project-dir> --mode deep_dive|tutor
pla check <project-dir>
pla render <project-dir> --mode deep_dive|tutor --approve-audio
pla export <project-dir>
pla run <project-dir> --modes deep_dive,tutor
```

`pla render` schlägt ohne `--approve-audio` fehl. Audio wird nie implizit erzeugt.

## 6. Datenmodelle

### 6.1 SourceDocument

```yaml
id: source_001
type: pdf | markdown | text | article | transcript | slide | interview | note | link
title: string
author: string | null
published_date: string | null
imported_at: string
language: de-DE | en-US | other
url: string | null
license_status: owned | public_domain | open_license | permission_granted | unknown | restricted
allowed_usage: private_learning | internal_review | publishable_summary | publishable_quotes | no_export
reliability: high | medium | low | unknown
text_hash: sha256:string
sections:
  - id: section_001
    heading: string | null
    text: string
    page_start: integer | null
    page_end: integer | null
    timestamp_start: string | null
    timestamp_end: string | null
quotes:
  - id: quote_001
    section_id: section_001
    text: string
    max_export_words: integer
uncertainties:
  - string
```

### 6.2 KnowledgeModel

```yaml
schema_version: 1
topic: string
central_question: string
audience_level: beginner | intermediate | advanced_without_prerequisites | advanced
style_profile_id: string
key_terms:
  - id: term_001
    term: string
    definition: string
    source_refs:
      - source_001#section_001
claims:
  - id: claim_001
    claim: string
    type: descriptive | causal | normative | historical | statistical
    confidence: high | medium | low
    evidence:
      - id: evidence_001
        source_ref: source_001#section_001
        strength: high | medium | low
        note: string
    counterpoints:
      - id: counter_001
        text: string
        source_ref: source_002#section_004
uncertainties:
  - id: uncertainty_001
    text: string
    reason: missing_data | conflicting_sources | weak_evidence | unclear_definition
learning_objectives:
  - id: lo_001
    objective: string
    bloom_level: remember | understand | apply | analyze | evaluate | create
misconceptions:
  - id: misconception_001
    misconception: string
    correction: string
examples:
  - id: example_001
    text: string
    source_ref: source_001#section_002 | null
```

### 6.3 EpisodePlan

```yaml
schema_version: 1
mode: deep_dive | tutor
length_minutes: integer
style_profile_id: string
hosts:
  - id: host_a
    role: narrator | explainer | skeptic | examiner | learner_proxy
structure:
  - id: segment_001
    kind: cold_open | question | background | claim | counterpoint | example | quiz | summary | conclusion
    target_minutes: number
    required_claim_ids:
      - claim_001
    required_learning_objective_ids:
      - lo_001
quality_gates:
  - source_check
  - depth_check
  - learning_check
  - redundancy_check
  - hallucination_check
```

## 7. Style Profiles

Der MVP enthält diese Profile:

```yaml
id: de_calm_deep
audio_language: de-DE
tone: calm_deep_essayistic
pace: medium
host_count: 2
humor: low
technical_depth: medium_high
```

```yaml
id: de_tutor_clear
audio_language: de-DE
tone: clear_supportive_tutor
pace: slow_medium
host_count: 2
humor: low
technical_depth: adaptive
pause_seconds_after_question: 3
```

## 8. Prompt- und Modellstrategie

Prompts sind versionierte Templates. Jede Stufe hat ein JSON/YAML-Schema und validiert ihre Ausgabe.

Pflichtstufen:

1. `extract_sources.v1`,
2. `build_source_index.v1`,
3. `build_knowledge_model.v1`,
4. `plan_deep_dive.v1`,
5. `plan_tutor.v1`,
6. `write_deep_dive_script.v1`,
7. `write_tutor_script.v1`,
8. `quality_review.v1`,
9. `export_learning_assets.v1`.

## 9. Quality Gates

| Gate | Blockierend | Regel |
| --- | --- | --- |
| `source_check` | Ja | Jede starke Skriptaussage muss eine Claim-ID oder Term-ID haben. |
| `evidence_check` | Ja | Jede Claim-ID braucht mindestens eine Evidence-ID. |
| `hallucination_check` | Ja | Neue Fakten im Skript ohne Wissensmodell-Referenz sind Fehler. |
| `rights_check` | Ja | Quellen mit `no_export` dürfen nicht in Show Notes oder Audio erscheinen. |
| `learning_check` | Ja für Tutor | Tutor-Skripte brauchen mindestens drei Lernziele und fünf Abruffragen. |
| `depth_check` | Warnung | Pro zentraler Frage muss mindestens ein Gegenargument oder eine Unsicherheit vorkommen. |
| `redundancy_check` | Warnung | Kein Abschnitt darf mehr als 30 Prozent Satzähnlichkeit zum direkt vorherigen Abschnitt haben. |
| `audio_readiness_check` | Warnung | Sprecherrollen, Pausen und Kapitelmarken müssen vollständig sein. |

Audio-Rendering ist blockiert, wenn ein blockierendes Gate fehlschlägt.

## 10. Rechte, Datenschutz und Sicherheit

### Rechte

- Standardstatus importierter Quellen ist `unknown`.
- Quellen mit `unknown` dürfen privat analysiert werden, aber nicht in öffentlich gedachte Show Notes exportiert werden.
- Quellen mit `restricted` oder `no_export` dürfen nicht direkt zitiert werden.
- Das System paraphrasiert standardmäßig und erzwingt kurze Zitate mit Quelle und Wortlimit.

### Datenschutz

- Lokale Projektdateien sind die primäre Persistenz.
- Logs dürfen keine vollständigen Quellentexte enthalten.
- Personenbezogene Daten werden vor Modellaufrufen durch eine Redaction-Stufe markiert.
- Nutzer kann Quellen mit `private: true` vom Export ausschließen.

### Transparenznotiz

Jeder Export enthält:

> Dieser Output ist eine quellengebundene Synthese. Er ersetzt keine fachliche, rechtliche, medizinische oder wissenschaftliche Begutachtung. Unsichere oder widersprüchliche Quellenlagen werden markiert.

## 11. Audio-Entscheidung

- Audio ist im MVP optional.
- Audio wird segmentweise gerendert.
- Cache-Key ist `sha256(voice_id + segment_text + tts_settings)`.
- Nur geänderte Segmente werden neu gerendert.
- Standardformat ist MP3, 44.1 kHz, Stereo, lautheitsnormalisiert auf -16 LUFS.
- Pausen im Tutor-Modus werden explizit als Stille gerendert.

## 12. Run Manifest

Jeder Lauf erzeugt ein Manifest:

```yaml
run_id: 2026-07-08T12-00-00Z
created_at: 2026-07-08T12:00:00Z
pipeline_version: 1
prompt_versions:
  extract_sources: v1
  build_knowledge_model: v1
  quality_review: v1
source_hashes:
  source_001: sha256:...
model_hashes:
  knowledge_model: sha256:...
outputs:
  knowledge_model: models/knowledge_model.yaml
  deep_dive_script: scripts/deep_dive_script.md
  tutor_script: scripts/tutor_script.md
quality_status: passed | warning | failed
audio_approved: false
```

## 13. Evaluationsdaten

Vor Feature-Erweiterungen müssen drei Fixture-Projekte existieren:

1. `fixtures/simple_topic`: zwei bis drei konsistente Quellen,
2. `fixtures/controversial_topic`: widersprüchliche Quellen,
3. `fixtures/exam_topic`: Lernstoff mit klaren Prüfungszielen.

Jedes Fixture enthält erwartete Claims, Lernziele, Gegenargumente und mindestens einen absichtlich problematischen Fall.

## 14. Definition of Done

Der MVP ist fertig, wenn:

- alle Pflichtartefakte erzeugt werden,
- alle YAML-Dateien gegen Schemas validieren,
- blockierende Quality Gates Audio verhindern,
- ein Run über das Manifest reproduzierbar ist,
- Skripte keine faktenbezogenen Aussagen ohne Wissensmodell-Referenz enthalten,
- Tutor-Skripte Lernziele, Pausen, Abruffragen und Musterantworten enthalten,
- optionale Audio-Exports nur mit expliziter Freigabe entstehen,
- Fixture-Projekte erfolgreich durch die Pipeline laufen.

## 15. Deep-Research-Definition

Der Rechercheteil ist nur dann ausreichend definiert, wenn er nicht bloß Zusammenfassungen erzeugt, sondern eine belastbare Wissensstruktur mit Quellenkritik, Argumentlogik und offenen Fragen.

### 15.1 Recherche-Tiefenstufen

| Stufe | Name | Mindestleistung |
| --- | --- | --- |
| 0 | Import | Quellen werden nur gespeichert und in Abschnitte zerlegt. |
| 1 | Summary | Quellen werden abschnittsweise zusammengefasst. |
| 2 | Claim Extraction | Aussagen, Begriffe, Beispiele und Evidenz werden extrahiert. |
| 3 | Argument Map | Claims werden mit Evidenz, Gegenargumenten und Unsicherheiten verbunden. |
| 4 | Synthesis | Widersprüche, Forschungslücken, Begriffsunterschiede und Abhängigkeiten werden erklärt. |
| 5 | Editorial Judgment | Das System priorisiert, was zentral, kontrovers, unsicher, überraschend und lernrelevant ist. |

Der MVP muss Stufe 3 erreichen. Stufe 4 ist für kontroverse Themen Pflicht, sobald mehr als drei Quellen verarbeitet werden. Stufe 5 ist Roadmap, darf aber bereits als Review-Hinweis erscheinen.

### 15.2 Verbindliche Research-Artefakte

Zusätzlich zum `KnowledgeModel` erzeugt Deep Research:

- `source_index.yaml`: Quellen, Metadaten, Rechte, Hashes und Reliability,
- `claim_graph.yaml`: Claims, Evidence, Counterpoints, Dependencies,
- `research_briefing.md`: verständliche Synthese,
- `open_questions.md`: offene Fragen, Widersprüche und Recherchebedarf,
- `editorial_priorities.yaml`: Wichtigkeit, Neuigkeitswert, Kontroversität und Lernrelevanz.

### 15.3 Quellenkritik

Jede Quelle erhält eine Bewertung nach diesen Kriterien:

- Nähe zur Primärquelle,
- Aktualität,
- methodische Qualität,
- mögliche Interessenlage,
- Widerspruch zu anderen Quellen,
- Relevanz für die zentrale Frage.

Claims aus schwachen Quellen dürfen im Skript nur mit sprachlicher Einschränkung verwendet werden.

## 16. Pädagogische Spezifikation

Der Tutor-Modus ist nur gut definiert, wenn er nicht einfach erklärt, sondern Lernen aktiv auslöst, überprüft und wiederholt.

### 16.1 Didaktisches Modell

Der MVP kombiniert vier Prinzipien:

1. **Learning Objectives:** Jede Folge beginnt mit aktiv überprüfbaren Lernzielen.
2. **Retrieval Practice:** Lernende müssen Wissen aktiv abrufen, bevor die Musterantwort kommt.
3. **Worked Examples:** Neue Konzepte werden an Beispielen und Gegenbeispielen erklärt.
4. **Misconception Repair:** Typische Fehlannahmen werden explizit diagnostiziert und korrigiert.

### 16.2 Tutor-Folgenstruktur

Jede Tutor-Folge muss diese Elemente enthalten:

1. Lernziel,
2. Vorwissensfrage,
3. kurze Erklärung,
4. Antwortpause,
5. Beispiel,
6. Gegenbeispiel,
7. Verständnisfrage,
8. typischer Fehler,
9. Korrektur des Fehlers,
10. Mini-Zusammenfassung,
11. Transferfrage,
12. Abrufquiz,
13. nächste Wiederholungsempfehlung.

### 16.3 Lernziel-Qualität

Lernziele sind ungültig, wenn sie nur „verstehen“ oder „kennenlernen“ sagen. Sie müssen beobachtbare Verben verwenden, etwa:

- erklären,
- unterscheiden,
- anwenden,
- vergleichen,
- bewerten,
- kritisch einordnen.

### 16.4 Fragenmix

Jede Tutor-Folge braucht mindestens:

- zwei Abruffragen,
- eine Verständnisfrage,
- eine Transferfrage,
- eine Fehlkonzept-Frage,
- eine Selbstbewertungsfrage.

## 17. Storytelling- und Dramaturgie-Spezifikation

Der Deep-Dive-Modus ist nur gut definiert, wenn er eine narrative und argumentative Dramaturgie besitzt, nicht nur eine lineare Inhaltsliste.

### 17.1 Deep-Dive-Dramaturgie

Jede Deep-Dive-Folge nutzt diese Makrostruktur:

1. **Cold Open:** konkrete Szene, Konflikt oder überraschender Befund.
2. **Promise:** Was kann der Hörer am Ende besser verstehen?
3. **Map:** Welche Route nimmt die Folge?
4. **Context:** historischer, technischer oder sozialer Hintergrund.
5. **Tension 1:** erste zentrale These mit Evidenz.
6. **Complication:** Gegenargument, Grenze oder Widerspruch.
7. **Tension 2:** tiefere Erklärung oder zweiter Blickwinkel.
8. **Case:** konkretes Beispiel oder Fallstudie.
9. **Synthesis:** Was folgt aus den konkurrierenden Perspektiven?
10. **Open Loop Closure:** Rückkehr zur Eingangsfrage.
11. **Afterthought:** ein prägnanter Schlussgedanke ohne falsche Eindeutigkeit.

### 17.2 Szenen statt Abschnitte

Ein Deep-Dive-Skript besteht aus Szenen. Jede Szene braucht:

- Funktion in der Dramaturgie,
- zentrale Frage,
- relevante Claims,
- Konflikt oder Erkenntnisfortschritt,
- Übergang zur nächsten Szene.

### 17.3 Host-Rollen

Der MVP nutzt zwei Rollen:

- **Host A:** erklärt, synthetisiert, führt durch die Geschichte.
- **Host B:** fragt kritisch nach, markiert Unsicherheiten, vertritt Hörerfragen.

Optional später:

- **Host C:** Gegenposition, Praxisbeispiel oder Prüfungsrolle.

### 17.4 Qualitätsregeln für Dialog

Dialog ist ungültig, wenn Host B nur Stichworte liefert. Host B muss mindestens eine dieser Funktionen erfüllen:

- Einwand,
- Präzisierung,
- Verständnisfrage,
- Gegenbeispiel,
- Quellenkritik,
- Transfer in Alltag oder Praxis.

### 17.5 Dramaturgische Qualitätsmetriken

| Metrik | Mindestwert |
| --- | --- |
| Hook vorhanden | Ja |
| Zentrale Frage innerhalb der ersten 90 Sekunden | Ja |
| Mindestens zwei Spannungsbögen | Ja |
| Mindestens ein echter Gegenstandpunkt | Ja |
| Mindestens ein konkretes Beispiel | Ja |
| Schluss greift Eingangsfrage wieder auf | Ja |
| Keine reine Listenstruktur über mehr als drei Minuten | Ja |

## 18. Gesamtbewertung des Definitionsstands

Nach diesen Ergänzungen ist der technische Kern gut definiert, die Pädagogik solide definiert und die Dramaturgie ausreichend spezifiziert, um erste Skriptgeneratoren zu bauen.

| Bereich | Bewertung | Begründung |
| --- | --- | --- |
| Deep Research | Gut, aber noch nicht wissenschaftlich vollständig | Claim Graph, Quellenkritik und offene Fragen sind definiert; systematische Literaturreview-Methodik ist nicht Teil des MVP. |
| Pädagogik | Gut für Selbstlernen | Lernziele, Retrieval Practice, Fehlkonzepte und Transferfragen sind Pflicht; adaptive Diagnostik bleibt Roadmap. |
| Storytelling | Gut für MVP-Skripte | Makrostruktur, Szenenlogik, Host-Rollen und Dramaturgie-Metriken sind festgelegt; Feinschliff bleibt redaktionell. |
| Dramaturgie | Gut messbar | Hook, zentrale Frage, Spannungsbögen, Gegenstandpunkt und Schlussrückbindung sind prüfbar. |
| Audio-Inszenierung | Mittel | Stimmen, Pausen und Kapitel sind definiert; Sounddesign, Musik und Performance-Regie sind noch Roadmap. |

Die wichtigste verbleibende Lücke ist nicht mehr die Spezifikation, sondern die spätere Evaluation an echten Fixture-Projekten: Erst Beispielquellen und Goldstandard-Ausgaben zeigen, ob Research, Didaktik und Dramaturgie wirklich gut genug umgesetzt sind.

## 19. Deep-Dive-Serienplanung und 30-Minuten-Grenze

Deep-Dive-Folgen sind im MVP auf maximal 30 Minuten begrenzt. Wenn ein Thema mehr Tiefe braucht, erzeugt das System keine überlange Einzelfolge, sondern plant eine Serie aus mehreren Folgen.

### 19.1 Harte Längenentscheidung

- `deep_dive` hat eine Zielspanne von 24 bis 30 Minuten.
- 30 Minuten sind die harte Obergrenze für eine einzelne Deep-Dive-Folge.
- Bei geschätzter Skriptlänge über 30 Minuten muss der Episode Planner splitten.
- Kürzungen dürfen nicht dazu führen, dass Gegenargumente, Unsicherheiten oder Quellenkritik entfernt werden.

### 19.2 Tiefe-Wunsch des Nutzers

Der Nutzer wählt vor der Planung eine gewünschte Tiefe:

```yaml
depth_request: overview | standard | deep | expert_series
```

| Tiefe | Planung |
| --- | --- |
| `overview` | Eine kompakte Folge mit Fokus auf zentrale Frage und wichtigste Claims. |
| `standard` | Eine Folge bis 30 Minuten mit Kontext, Belegen, Gegenposition und Beispiel. |
| `deep` | Zwei bis drei Folgen: Einstieg, Vertiefung, Konsequenzen oder offene Fragen. |
| `expert_series` | Mehrteilige Serie mit separaten Folgen für Grundlagen, Kontroversen, Methoden, Fälle und Ausblick. |

### 19.3 Komplexitätsbewertung

Vor der Episodenplanung berechnet das System eine Themenkomplexität.

```yaml
complexity_score:
  source_count: integer
  claim_count: integer
  counterpoint_count: integer
  uncertainty_count: integer
  key_term_count: integer
  prerequisite_count: integer
  controversy_level: low | medium | high
  estimated_minutes: integer
  recommended_episode_count: integer
```

### 19.4 Split-Regeln

Das System erzeugt mehrere Deep-Dive-Folgen, wenn mindestens eine Bedingung erfüllt ist:

- `estimated_minutes > 30`,
- `depth_request` ist `deep` oder `expert_series`,
- mehr als acht zentrale Claims vorhanden sind,
- mehr als vier zentrale Gegenargumente vorhanden sind,
- mehr als sechs Schlüsselbegriffe erklärt werden müssen,
- die Quellenlage `controversy_level: high` hat.

### 19.5 Serientypen

Jede Folge einer Serie erhält einen Zweck:

| Serientyp | Zweck |
| --- | --- |
| `foundation` | Grundlagen, zentrale Frage, Begriffe und Kontext. |
| `deepening` | Vertiefung eines Claims, Mechanismus oder Forschungsstrangs. |
| `extension` | Erweiterung auf Anwendungsfälle, Nachbarfragen oder Praxisfolgen. |
| `continuation` | Fortsetzung einer noch nicht abgeschlossenen Argumentation. |
| `counterpoint` | Gegenpositionen, Kontroversen und Unsicherheiten. |
| `case_study` | Konkretes Beispiel oder Fallanalyse. |
| `synthesis` | Zusammenführung, Konsequenzen, offene Fragen und Ausblick. |

### 19.6 SeriesPlan

Bei mehr als einer Folge erzeugt die Planung zusätzlich `series_plan.yaml`.

```yaml
schema_version: 1
topic: string
depth_request: standard | deep | expert_series
complexity_score:
  estimated_minutes: 86
  recommended_episode_count: 3
series:
  - episode_number: 1
    mode: deep_dive
    series_type: foundation
    title: string
    central_question: string
    target_minutes: 28
    included_claim_ids:
      - claim_001
    deferred_claim_ids:
      - claim_006
    handoff_question: string
  - episode_number: 2
    mode: deep_dive
    series_type: deepening
    title: string
    central_question: string
    target_minutes: 29
  - episode_number: 3
    mode: deep_dive
    series_type: synthesis
    title: string
    central_question: string
    target_minutes: 27
```

### 19.7 Dramaturgie über mehrere Folgen

Eine Serie braucht nicht nur einzelne gute Folgen, sondern einen Bogen über die gesamte Staffel:

- Folge 1 beantwortet die Grundfrage nur teilweise und markiert bewusst offene Schleifen.
- Vertiefungsfolgen lösen je eine zentrale offene Schleife.
- Erweiterungsfolgen übertragen das Thema auf neue Kontexte.
- Fortsetzungsfolgen führen eine unterbrochene Argumentation weiter.
- Die letzte Folge enthält Synthese, Rückblick und offene Forschungs- oder Praxisfragen.

### 19.8 Quality Gate für Serienplanung

`series_planning_check` ist blockierend für Deep-Dive-Ausgaben.

| Regel | Blockierend |
| --- | --- |
| Keine Deep-Dive-Folge überschreitet 30 Minuten Zielzeit. | Ja |
| Jede ausgelagerte zentrale These erscheint in einer späteren Folge oder wird bewusst verworfen. | Ja |
| Jede Folge hat einen eigenen Zweck und eine eigene zentrale Frage. | Ja |
| Eine Serie hat einen übergreifenden Spannungsbogen. | Warnung |
| Die letzte Folge enthält Synthese oder bewussten Ausblick. | Warnung |
