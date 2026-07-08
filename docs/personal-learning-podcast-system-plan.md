# Implementierungsplan: Personal Learning Podcast System

## Zielbild

Die verbindlichen Produkt- und Architekturentscheidungen stehen in [`SPEC.md`](../SPEC.md). Dieses Dokument bleibt der ausführliche Implementierungsplan und erklärt Roadmap, Module und Begründungen.

Das System soll kein reiner Podcast-Generator sein, sondern ein Recherche-zu-Audio-Lernsystem. Ein gemeinsamer Recherche-Kern erzeugt zuerst eine strukturierte Wissensbasis. Aus dieser Wissensbasis entstehen anschließend verschiedene Ausgabeformate wie Deep-Dive-Podcast, Lernfolge, Quizfolge, Review-Folge, Debatte oder Prüfungsmodus.

## Leitprinzipien

1. **Single Source of Truth:** Jede Episode basiert auf einem versionierten Wissensmodell.
2. **Quellenbindung:** Jede starke Aussage muss auf mindestens eine Quelle zurückführbar sein.
3. **Mehrere Ausgabeformate:** Deep-Dive und Lernmodus nutzen dieselbe Recherche, aber unterschiedliche Dramaturgie.
4. **Lernorientierung:** Tutor-, Exam- und Review-Modi definieren aktiv überprüfbare Lernziele.
5. **Kontrollpunkte vor Audio:** Faktencheck, Redundanzcheck und Lerncheck passieren vor der Audio-Produktion.
6. **Iteratives Feedback:** Nutzerfeedback verbessert Wissensmodell, Skripte, Fragen und zukünftige Folgen.

## Zielarchitektur

```text
Quellen
  ↓
Ingestion
  ↓
Quellenanalyse
  ↓
Wissensmodell
  ↓
Redaktionelle Planung
  ↓
Format-Router
  ├─ Deep-Dive-Podcast
  ├─ Lern-Podcast / Audio-Tutor
  ├─ Quizfolge
  ├─ Review-Folge
  ├─ Debattenmodus
  └─ Prüfungsmodus
  ↓
Skript
  ↓
Fakten- und Qualitätschecks
  ↓
Audio-Produktion
  ↓
Export + Feedback + Wiederholung
```

## Datenmodell

### SourceDocument

Speichert Rohmaterial und Metadaten.

```yaml
id: source_001
type: pdf | article | note | transcript | slide | interview | link
title: "Titel der Quelle"
author: "Autor oder Organisation"
date: "2026-07-08"
url: "https://example.com"
reliability: high | medium | low | unknown
text_hash: "sha256"
sections:
  - id: section_001
    heading: "Abschnittstitel"
    text: "Bereinigter Abschnitt"
important_quotes:
  - quote: "Kurzes Zitat"
    section_id: section_001
uncertainties:
  - "Unklare Studienlage zu ..."
```

### KnowledgeModel

Die zentrale strukturierte Wissensbasis.

```yaml
topic: "KI in der Bildung"
central_question: "Macht KI Lernen besser oder nur bequemer?"
audience_level: "fortgeschritten, aber ohne Vorwissen"
key_terms:
  - term: "Feedbackqualität"
    definition: "..."
claims:
  - id: claim_001
    claim: "KI kann individuelles Feedback verbessern."
    confidence: medium
    evidence:
      - source_id: source_001
        section_id: section_003
        strength: high
    counterpoints:
      - "Automatisiertes Feedback kann falsch oder zu allgemein sein."
open_questions:
  - "Welche Effekte bleiben nach mehreren Monaten stabil?"
learning_objectives:
  - id: lo_001
    objective: "Erklären, warum Feedback beim Lernen zentral ist."
misconceptions:
  - "Mehr Erklärungen bedeuten automatisch besseres Lernen."
examples:
  - "KI-Tutor gibt adaptives Feedback nach einer falschen Mathe-Antwort."
```

### EpisodePlan

Format-spezifische redaktionelle Planung.

```yaml
mode: deep_dive | tutor | quiz | review | debate | exam
length_minutes: 35
hosts:
  - name: Host A
    role: explanation
  - name: Host B
    role: critical_questions
structure:
  - cold_open
  - central_question
  - background
  - claim_001
  - counterposition
  - case_study
  - conclusion
required_claims:
  - claim_001
quality_gates:
  - source_check
  - depth_check
  - hallucination_check
```

## Module

### Modul 1: Ingestion

**Aufgabe:** Quellen importieren, normalisieren und segmentieren.

**Inputs:** PDFs, Artikel, Links, Notizen, YouTube-Transkripte, Vorlesungsfolien, Interviews.

**Outputs:**

- bereinigter Text,
- Quellenliste,
- Abschnitte,
- wichtige Zitate,
- Metadaten,
- initiale Unsicherheiten.

**Akzeptanzkriterien:**

- Jede Quelle erhält eine stabile ID.
- Jeder Textabschnitt ist auf die Originalquelle rückführbar.
- Duplikate werden erkannt.
- Importfehler werden sichtbar protokolliert.

### Modul 2: Quellenanalyse

**Aufgabe:** Aus Quellen Themen, Begriffe, Thesen, Belege, Gegenargumente und offene Fragen extrahieren.

**Outputs:**

- zentrale Themen,
- Kernthesen,
- Belege,
- Gegenargumente,
- offene Fragen,
- Begriffsdefinitionen,
- Fallbeispiele,
- Widersprüche,
- Unsicherheiten.

**Akzeptanzkriterien:**

- Jede extrahierte These referenziert mindestens einen Quellenabschnitt.
- Widersprüche zwischen Quellen werden markiert.
- Unsichere Aussagen erhalten eine niedrigere Konfidenz.

### Modul 3: Wissensmodell

**Aufgabe:** Analyseergebnisse in eine versionierte, maschinenlesbare Single Source of Truth überführen.

**Outputs:**

- `knowledge_model.yaml` oder JSON,
- Quellen-Mapping,
- Lernziele,
- Missverständnisse,
- Transferaufgaben,
- offene Recherchefragen.

**Akzeptanzkriterien:**

- Das Wissensmodell validiert gegen ein Schema.
- Starke Aussagen ohne Quelle werden blockiert.
- Lernziele sind aktiv formuliert, etwa „erklären“, „vergleichen“, „anwenden“.

### Modul 4: Format-Router

**Aufgabe:** Aus Nutzerwunsch oder Zeitplan passende Ausgabeformate auswählen.

**Modi:**

- Explore Mode / Deep-Dive,
- Tutor Mode,
- Socratic Mode,
- Exam Mode,
- Review Mode,
- Debate Mode,
- Kurzbriefing.

**Akzeptanzkriterien:**

- Derselbe Recherche-Kern kann mehrere Episoden erzeugen.
- Jeder Modus verwendet eigene Strukturregeln.
- Länge, Niveau und Ton werden explizit gesetzt.


### Serienplanung für Deep-Dive-Folgen

Deep-Dive-Folgen dürfen nicht länger als 30 Minuten werden. Wenn die Komplexität des Themas oder der gewünschte Tiefgang mehr Raum braucht, plant das System automatisch mehrere Folgen. Diese Folgen müssen klar als Grundlagenfolge, Vertiefung, Erweiterung, Fortsetzung, Gegenposition, Fallstudie oder Synthese markiert werden.

Die Planung hängt von zwei Faktoren ab:

1. **Themenkomplexität:** Anzahl der Quellen, Claims, Gegenargumente, Unsicherheiten, Schlüsselbegriffe und notwendigen Voraussetzungen.
2. **Tiefe-Wunsch:** `overview`, `standard`, `deep` oder `expert_series`.

Das Ergebnis ist bei Bedarf ein `series_plan.yaml`, das pro Folge zentrale Frage, Zweck, Zielzeit, enthaltene Claims, vertagte Claims und Übergangsfrage definiert. Keine einzelne Deep-Dive-Folge darf die 30-Minuten-Grenze überschreiten.

### Modul 5: Redaktionelle Planung

**Aufgabe:** Format-spezifische Episodenstruktur aus dem Wissensmodell erzeugen.

**Deep-Dive-Struktur:**

1. Cold Open,
2. zentrale Frage,
3. Hintergrund,
4. These,
5. Belege,
6. Gegenposition,
7. Vertiefung,
8. Fallbeispiel,
9. Konsequenzen,
10. offene Fragen,
11. Schlussgedanke.

**Lernfolge-Struktur:**

1. Lernziel,
2. Vorwissensfrage,
3. kurze Erklärung,
4. Pause zum Antworten,
5. Beispiel,
6. Gegenbeispiel,
7. Verständnisfrage,
8. typischer Fehler,
9. Mini-Zusammenfassung,
10. Transferfrage,
11. Abrufquiz.

**Akzeptanzkriterien:**

- Deep-Dive-Folgen enthalten echte Gegenpositionen.
- Lernfolgen enthalten Abruf-, Verständnis- und Transferfragen.
- Pausen und Wiederholungen sind im Skript markiert.

### Modul 6: Skripterstellung

**Aufgabe:** EpisodePlan in ein sprechbares Mehrstimmen-Skript umsetzen.

**Outputs:**

- Skript mit Sprecherrollen,
- Pausenmarkierungen,
- Kapitelmarken,
- Quellenhinweisen,
- Show Notes,
- Quizfragen und Karteikarten.

**Akzeptanzkriterien:**

- Skript klingt dialogisch statt wie ein Lexikonartikel.
- Jede zentrale Behauptung bleibt mit dem Wissensmodell verknüpft.
- Lernfragen enthalten Musterantworten.

### Modul 7: Fakten- und Qualitätschecks

**Checks:**

1. Quellencheck: Hat jede starke Aussage eine Quelle?
2. Tiefencheck: Gibt es echte Gegenargumente?
3. Lerncheck: Gibt es klare Lernziele?
4. Verständnischeck: Muss der Hörer aktiv mitdenken?
5. Redundanzcheck: Wiederholt sich die Folge unnötig?
6. Audio-Check: Klingt das Skript natürlich?
7. Halluzinationscheck: Gibt es Aussagen ohne Quellenbasis?

**Akzeptanzkriterien:**

- Fehlerhafte oder unbelegte Aussagen blockieren die Audio-Produktion.
- Warnungen werden in einem Review-Bericht ausgegeben.
- Nutzer kann Korrekturen vor der Audio-Erzeugung freigeben.

### Modul 8: Audio-Produktion

**Aufgabe:** Skripte in Audio umwandeln und schneiden.

**Outputs:**

- MP3 oder WAV,
- Kapitelmarken,
- Intro/Outro,
- Pausen,
- Lautheitsnormalisierung,
- Show Notes,
- Transkript.

**Akzeptanzkriterien:**

- Mehrere Stimmen sind eindeutig unterscheidbar.
- Lernpausen sind hörbar eingebaut.
- Kapitelmarken passen zur Episodenstruktur.

### Modul 9: Feedback und Wiederholung

**Aufgabe:** Nutzerfeedback, Quiz-Ergebnisse und Wiederholungspläne in den Prozess zurückführen.

**Outputs:**

- Feedbackprotokoll,
- aktualisierte Lernziele,
- Review-Folgen nach 1, 3, 7 und 14 Tagen,
- Karteikartenexport.

**Akzeptanzkriterien:**

- Falsch beantwortete Fragen werden bevorzugt wiederholt.
- Review-Folgen fokussieren auf Kernbegriffe und typische Fehler.
- Das Wissensmodell bleibt nachvollziehbar versioniert.


## Bewertung: Löcher, Lücken und fehlende Entscheidungen

Der bisherige Plan beschreibt die gewünschte Zielarchitektur, lässt aber mehrere Produkt-, Architektur- und Betriebsentscheidungen offen. Diese Punkte sollten vor oder während der MVP-Umsetzung explizit entschieden werden, damit das System nicht zu einem schwer testbaren Prompt-Workflow ohne klare Qualitätsgrenzen wird.

### 1. Unklare Produktgrenzen

**Lücke:** Der Plan nennt viele Modi, aber priorisiert noch nicht hart genug, welche Modi im MVP wirklich gebaut werden und welche nur Roadmap sind.

**Risiko:** Das Team baut zu früh Deep-Dive, Tutor, Quiz, Review, Debate und Exam gleichzeitig. Dadurch wird der gemeinsame Recherche-Kern nicht stabil genug, bevor Formatlogik dazukommt.

**Entscheidung:** Für den MVP sind nur drei Outputs verpflichtend:

1. Wissensmodell,
2. Deep-Dive-Skript,
3. Tutor-Skript mit Quizfragen.

Quizfolge, Review-Folge, Debate Mode und Exam Mode bleiben zunächst Export- oder Planungsartefakte, aber keine vollständigen Audioformate.

### 2. Fehlende Nutzerrollen und Use Cases

**Lücke:** Der Plan sagt „Nutzer“, unterscheidet aber nicht zwischen Lernendem, Redakteur, Lehrkraft, Forscher oder Content-Creator.

**Risiko:** Anforderungen widersprechen sich. Ein Lernender braucht Einfachheit und Wiederholung, ein Redakteur braucht Kontrolle, Quellenprüfung und Eingriffsmöglichkeiten.

**Entscheidung:** Der MVP optimiert für eine einzelne Rolle: ein fortgeschrittener Selbstlerner, der eigene Quellen hochlädt und daraus Lern- und Erklär-Audio erzeugt. Redaktionelle Teamfunktionen, Kollaboration und Freigabe-Workflows kommen später.

### 3. Fehlende Entscheidung zur technischen Schnittstelle

**Lücke:** Es ist noch offen, ob das System zuerst CLI, Web-App, API oder Workflow-Automation sein soll.

**Risiko:** Ohne klare Oberfläche werden Datenmodell, Dateistruktur, Statusverwaltung und Fehlerbehandlung inkonsistent.

**Entscheidung:** Der MVP startet als CLI-Workflow mit projektbasierter Ordnerstruktur. Eine Web-App wird erst gebaut, wenn die Pipeline reproduzierbar ist.

```text
project/
  sources/
  models/
  episodes/
  exports/
  reports/
```

### 4. Fehlende Persistenz- und Versionierungsstrategie

**Lücke:** Der Plan erwähnt Versionierung, entscheidet aber nicht, wie Modelle, Quellenstände und Episodenstände gespeichert werden.

**Risiko:** Skripte lassen sich später nicht reproduzieren. Feedback kann nicht sauber auf den damaligen Quellenstand bezogen werden.

**Entscheidung:** Jede Pipeline-Ausführung erzeugt einen unveränderlichen Run-Ordner mit Manifest.

```yaml
run_id: 2026-07-08T12-00-00Z
source_hashes:
  - source_001: sha256:...
knowledge_model_version: v1
prompts_version: v1
outputs:
  - deep_dive_script.md
  - tutor_script.md
```

### 5. Zu grobes Quellen- und Rechtekonzept

**Lücke:** Der Plan behandelt Quellen technisch, aber nicht ausreichend rechtlich und operativ.

**Risiko:** Volltexte, Buchkapitel, Transkripte oder Artikel können urheberrechtlich problematisch sein. Außerdem ist unklar, was in Show Notes oder Skripten zitiert werden darf.

**Entscheidung:** Das System speichert für jede Quelle Nutzungsstatus und Zitatgrenzen. Exportierte Skripte verwenden standardmäßig Paraphrasen und kurze Zitate. Der Nutzer muss bei geschützten Quellen bestätigen, dass er sie verwenden darf.

### 6. Unpräzise Qualitätsmetriken

**Lücke:** Checks wie „Tiefencheck“ oder „Audio-Check“ sind sinnvoll, aber noch nicht messbar genug.

**Risiko:** Qualitäts-Gates werden zu subjektiven LLM-Urteilen ohne reproduzierbare Schwellenwerte.

**Entscheidung:** Jeder Check erhält maschinenprüfbare Mindestkriterien.

| Check | Mindestkriterium im MVP |
| --- | --- |
| Quellencheck | Jede Claim-ID im Skript muss mindestens eine Evidence-ID haben. |
| Tiefencheck | Pro zentraler Frage mindestens ein Gegenargument oder eine Unsicherheit. |
| Lerncheck | Jede Tutor-Folge braucht mindestens drei Lernziele und fünf Abruffragen. |
| Redundanzcheck | Keine identischen Kernthesen in mehr als zwei aufeinanderfolgenden Abschnitten. |
| Halluzinationscheck | Neue Fakten im Skript ohne Claim-ID werden als Fehler markiert. |

### 7. Fehlende Human-in-the-Loop-Entscheidung

**Lücke:** Es ist offen, ob die Pipeline vollautomatisch bis MP3 laufen soll oder ob Nutzer vor Audio freigeben müssen.

**Risiko:** Fehler im Wissensmodell werden teuer, sobald daraus Audio erzeugt wird.

**Entscheidung:** Der MVP stoppt standardmäßig nach Wissensmodell und Skript zur manuellen Freigabe. Audio-Produktion startet nur mit explizitem `--approve-audio` oder nach bestandenem Review-Bericht ohne blockierende Fehler.

### 8. Fehlendes Fehler- und Unsicherheitsdesign

**Lücke:** Der Plan nennt Unsicherheiten, definiert aber nicht, wie sie im Podcast erscheinen.

**Risiko:** Unsichere Quellenlagen werden entweder verschwiegen oder klingen zu endgültig.

**Entscheidung:** Unsicherheiten sind erstklassige Inhalte. Das Skript muss sie sprachlich markieren, etwa „Die Studienlage ist hier gemischt“ oder „Diese Quelle zeigt nur eine kurzfristige Wirkung“.

### 9. Fehlende Prompt- und Modellstrategie

**Lücke:** Der Plan erwähnt keine Trennung zwischen Extraktions-, Analyse-, Planungs-, Schreib- und Prüfprompts.

**Risiko:** Ein monolithischer Prompt wird unwartbar und schwer testbar.

**Entscheidung:** Die Pipeline nutzt getrennte Prompt-Stufen mit versionierten Templates:

- `extract_sources`,
- `build_claims`,
- `build_learning_objectives`,
- `plan_episode`,
- `write_script`,
- `quality_review`.

Jede Stufe erhält klar definierte Eingaben, Ausgaben und Schema-Validierung.

### 10. Fehlende Evaluationsdaten

**Lücke:** Es gibt noch keine Goldstandard-Beispiele, an denen geprüft wird, ob die Pipeline besser wird.

**Risiko:** Verbesserungen werden nach Gefühl bewertet.

**Entscheidung:** Vor der Implementierung werden mindestens drei Testprojekte angelegt:

1. ein kurzes Thema mit 2-3 Quellen,
2. ein kontroverses Thema mit widersprüchlichen Quellen,
3. ein Lernstoff-Thema mit klaren Prüfungszielen.

Für jedes Testprojekt gibt es erwartete Claims, Lernziele und Qualitätsprobleme.

### 11. Fehlende Audio-Kosten- und Latenzentscheidung

**Lücke:** TTS wird genannt, aber Kosten, Laufzeit, Caching und Wiederverwendung werden nicht entschieden.

**Risiko:** Jede kleine Skriptänderung erzeugt unnötig teure neue Audiodateien.

**Entscheidung:** Audio wird abschnittsweise gerendert und über Abschnitts-Hashes gecacht. Nur geänderte Abschnitte werden neu erzeugt.

### 12. Fehlende Grenzen der Automatisierung

**Lücke:** Der Plan erklärt, was das System erzeugen soll, aber nicht, was es bewusst nicht leisten darf.

**Risiko:** Nutzer vertrauen dem Output als geprüfter Wahrheit, obwohl es sich um quellengebundene Synthese handelt.

**Entscheidung:** Jeder Export enthält eine Transparenznotiz: Das System fasst Quellen zusammen, markiert Unsicherheiten und ersetzt keine fachliche, rechtliche, medizinische oder wissenschaftliche Begutachtung.

### 13. Fehlende Internationalisierungs- und Stilentscheidung

**Lücke:** Sprache, Tonalität und Zielniveau sind konfigurierbar gedacht, aber nicht formalisiert.

**Risiko:** Episoden wechseln Stil und Anspruchsniveau unkontrolliert.

**Entscheidung:** Jede Episode benötigt ein Style Profile.

```yaml
language: de-DE
audience_level: advanced_without_prerequisites
tone: calm_deep_essayistic
pace: medium
host_count: 2
humor: low
technical_depth: medium_high
```

### 14. Fehlende Datenschutzentscheidung

**Lücke:** Eigene Notizen, Interviews und Lernfeedback können personenbezogene oder vertrauliche Daten enthalten.

**Risiko:** Sensible Daten landen unkontrolliert in Prompts, Logs oder Exporten.

**Entscheidung:** Der MVP braucht mindestens eine lokale Redaction-Stufe für personenbezogene Daten, eine klare Log-Policy und eine Option, Quellen vom Audio- oder Show-Notes-Export auszuschließen.

### 15. Wichtigste Architekturentscheidung vor Start

Die kritischste offene Entscheidung ist nicht die TTS-Stimme, sondern die Verbindlichkeit des Wissensmodells. Wenn das Wissensmodell nur ein Zwischenprompt ist, verliert das System Nachvollziehbarkeit. Wenn es dagegen ein validiertes, versioniertes Artefakt mit Claim-IDs, Evidence-IDs, Unsicherheiten und Lernzielen ist, können alle Ausgabeformate stabil darauf aufbauen.

**Empfehlung:** Vor jeder Audio-Funktion zuerst Schema, Validierung, Run-Manifest und Quellenbindung bauen. Danach erst Deep-Dive- und Tutor-Skripte generieren.

## MVP-Umfang

Der erste realistische MVP sollte bewusst schmal sein und die oben genannten offenen Entscheidungen berücksichtigen:

1. CLI-basierter Projektordner für Markdown, Text und PDF.
2. Extraktion in bereinigte Abschnitte mit stabilen Quellen- und Abschnitts-IDs.
3. Quellenliste mit Hashes, Nutzungsstatus und Zitatgrenzen.
4. Automatisches Recherche-Briefing.
5. Argumentkarte mit Thesen, Belegen, Gegenargumenten und Unsicherheiten.
6. Versioniertes Wissensmodell mit Schema-Validierung und Run-Manifest.
7. Lernziele, Missverständnisse und Abruffragen.
8. Zwei Skriptgeneratoren:
   - Deep-Dive-Skript,
   - Tutor-Skript mit Quizfragen.
9. Faktencheck auf Quellenabdeckung, Claim-IDs und neue unbelegte Fakten.
10. Review-Bericht mit blockierenden Fehlern und Warnungen.
11. Optionaler TTS-Export mit zwei bis drei Stimmen nach expliziter Audio-Freigabe.
12. Export von Show Notes, Quizfragen und Karteikarten.

## Umsetzungsphasen

### Phase 1: Fundament

- Repository-Struktur für Quellen, Modelle, Episoden und Exporte definieren.
- Schemata für SourceDocument, KnowledgeModel und EpisodePlan erstellen.
- CLI- oder Workflow-Befehl für Ingestion bauen.
- Validierung und Fehlerberichte einführen.

### Phase 2: Recherche-Kern

- Quellenanalyse implementieren.
- Thesen, Belege, Gegenargumente und Unsicherheiten extrahieren.
- KnowledgeModel erzeugen und versionieren.
- Quellenbindung für Claims erzwingen.

### Phase 3: Zwei Hauptformate

- Deep-Dive-Planer und Skriptgenerator implementieren.
- Tutor-Planer und Skriptgenerator implementieren.
- Lernfragen, Pausen, Musterantworten und Transferfragen erzeugen.
- Show Notes und Kapitelmarken ausgeben.

### Phase 4: Qualitätssicherung

- Quellencheck, Tiefencheck, Lerncheck und Halluzinationscheck automatisieren.
- Review-Bericht erzeugen.
- Blockierende Fehler von Warnungen unterscheiden.
- Manuelle Freigabe vor Audio-Produktion ermöglichen.

### Phase 5: Audio und Export

- TTS-Anbindung integrieren.
- Stimmen je Sprecherrolle konfigurieren.
- Pausen, Intro, Outro und Lautheitsnormalisierung automatisieren.
- MP3, Transkript, Kapitelmarken, Quiz und Karteikarten exportieren.

### Phase 6: Lernsystem

- Quizfolge, Review-Folge und Exam Mode ergänzen.
- Spaced-Repetition-Zeitplan erzeugen.
- Nutzerfeedback und Quiz-Ergebnisse speichern.
- Wiederholungsfolgen automatisch planen.

## Priorisierte Backlog-Epics

1. **Source Ingestion:** Dateien und Links importieren, bereinigen und segmentieren.
2. **Citation Graph:** Aussagen, Belege und Quellenabschnitte verknüpfen.
3. **Knowledge Model Builder:** strukturierte Wissensbasis erstellen.
4. **Deep-Dive Generator:** erzählerische Podcastfolge planen und schreiben.
5. **Tutor Generator:** Lernfolge mit Fragen, Pausen und Musterantworten erstellen.
6. **Quality Gates:** Quellen-, Lern-, Redundanz- und Halluzinationschecks.
7. **Audio Renderer:** Skript in Mehrstimmen-Audio exportieren.
8. **Learning Exports:** Quiz, Karteikarten und Review-Zeitplan erzeugen.

## Definition of Done für den MVP

Ein Nutzer kann einen Quellenordner bereitstellen und erhält automatisch:

- ein validiertes Wissensmodell,
- ein Run-Manifest,
- ein Recherche-Briefing,
- eine Argumentkarte,
- ein Deep-Dive-Skript,
- ein Tutor-Skript mit Quizfragen,
- einen Quellen- und Qualitätsbericht,
- Show Notes,
- Quizfragen,
- Karteikarten,
- optional eine MP3-Ausgabe nach expliziter Freigabe.

Der MVP gilt nur als fertig, wenn zentrale Aussagen im Skript nachvollziehbar auf Quellen im Wissensmodell zurückverweisen, blockierende Qualitätsfehler die Audio-Produktion stoppen und die Ausgabe anhand eines Run-Manifests reproduzierbar ist.
