# Personal Learning Podcast System

Ein CLI-first System, das Quellen in ein strukturiertes Wissensmodell verwandelt und daraus Deep-Dive- und Tutor-Podcast-Skripte sowie optionale Audio-Exports erzeugt.

## Kernidee

Das Projekt behandelt Podcast und Lernen nicht als zwei getrennte Workflows. Stattdessen gibt es einen gemeinsamen Recherche-Kern:

```text
Quellen -> Ingestion -> Wissensmodell -> Formatplanung -> Skripte -> Quality Gates -> optional Audio
```

Das `KnowledgeModel` ist die Single Source of Truth. Alle starken Aussagen in Skripten müssen auf Claims, Begriffe, Beispiele oder Unsicherheiten im Wissensmodell zurückgeführt werden.

## MVP

Der MVP erzeugt verpflichtend:

- `knowledge_model.yaml`,
- `research_briefing.md`,
- `argument_map.md`,
- `deep_dive_script.md`,
- `tutor_script.md`,
- `quiz_cards.yaml`,
- `show_notes.md`,
- `quality_report.yaml`,
- `run_manifest.yaml`.

Audio ist optional und wird nur nach expliziter Freigabe erzeugt.

## Geplante CLI

```bash
pla init ./my-topic
pla ingest ./my-topic
pla model ./my-topic
pla plan ./my-topic --mode deep_dive
pla script ./my-topic --mode deep_dive
pla check ./my-topic
pla render ./my-topic --mode deep_dive --approve-audio
pla export ./my-topic
```

## Projektstruktur

```text
project/
  sources/
    raw/
    processed/
  models/
  scripts/
  reports/
  exports/
  runs/
```

## Qualitätsprinzipien

- Jede starke Aussage braucht Quellenbindung.
- Neue Fakten im Skript ohne Wissensmodell-Referenz blockieren den Export.
- Tutor-Folgen brauchen Lernziele, Abruffragen, Pausen und Musterantworten.
- Quellenrechte und Datenschutz werden als Metadaten abgebildet.
- Audio wird segmentweise gecacht und nur nach Freigabe gerendert.
- Deep-Dive-Folgen sind auf 30 Minuten begrenzt; komplexe Themen werden als Serie geplant.

## Dokumentation

- [SPEC.md](SPEC.md): verbindliche Produktspezifikation und Architekturentscheidungen.
- [docs/personal-learning-podcast-system-plan.md](docs/personal-learning-podcast-system-plan.md): ausführlicher Implementierungsplan mit Gap-Analyse und Roadmap.
- [docs/system-quality-assessment.md](docs/system-quality-assessment.md): Bewertung von Deep Research, Pädagogik, Storytelling und Dramaturgie.

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Siehe [LICENSE](LICENSE).
