# Gezielte Recherche und Wiederaufnahme

Die produktive Recherche verwendet feste Teilfragen, gezieltes Nachlesen, unabhängige Antwortprüfungen und gespeicherte Einzelabschlüsse. Der frühere Runden-Controller wurde entfernt. Alte Rechercheläufe werden weiterhin über `bootstrap_legacy` eingelesen und mit dem aktuellen Ablauf fortgesetzt; Quellen, gültige Entwürfe und verbrauchte Aufrufe bleiben erhalten.

## Automatisierte Regressionen

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_research tests.test_question_research tests.test_research_quality tests.test_research_refinement tests.test_research_invariants tests.test_research_migration -v
node --test tests/studio_ui.test.cjs
```

Alle Python-Fälle gehören auch zur regulären Testsuite. Sie prüfen mit temporären Projekten und simulierten Modellen insbesondere:

- Auffindbarkeit gespeicherter Abschnitte, Seitenangaben, Nachbarkontext und Leselimits.
- Schutz unbeteiligter Befunde und erneute Prüfung geänderter Belege.
- Quellen- und Aufruflimits sowie Wiederaufnahme ohne wiederholte Suchen oder Downloads.
- Vollständigkeit der ursprünglichen Leitfragen, unabhängige Prüfung und begrenztes Wiederöffnen.
- Import historischer Entwurfs- und Reparaturformate, unveränderte Quellen und Prüfsummen sowie das Fortsetzen vor und nach der alten Review-Stufe.

Migrationstests erzeugen die historischen Dateien direkt. Eine zweite ausführbare Recherchepipeline und Tests ihrer inzwischen ungenutzten Steuerungslogik sind dafür nicht erforderlich. Den heutigen Rechercheablauf beschreiben [docs/research.md](../../docs/research.md) und [docs/research-evidence.md](../../docs/research-evidence.md); die Analyse vom 17.09.2026 mit dem Hintergrund der ursprünglichen Fehler wurde am 19.09.2026 entfernt und bleibt in der Git-Historie (`docs/research-analysis.md`). Das separate Akzeptanzskript wurde in die Invariantentests aufgenommen.

## Kontrolle mit einem gespeicherten Quellenbestand

`check_saved_reader.py` arbeitet ausschließlich lesend: keine Modellaufrufe, Downloads oder Änderungen am Forschungsauftrag.

```powershell
.\.venv\Scripts\python.exe evals/research_refinement/check_saved_reader.py projects/mir-gehts-um-die-inhalte-der-doumente-di-c85f45 run_20260916_121103_388868_a082fa2a --query "Max-Neef Human Scale Development singular satisfiers synergic satisfiers definitions" --key-term "singular satisfiers" --key-term "synergic satisfiers" --expect-reference "src_6622dc67b5fea004#sec_f1dc13c2699a5406"
```

Der gespeicherte Prüffall enthält **37 Quellen und 4.275 Abschnitte**. Die erwartete Originaldefinition auf Seite 24 erreicht **Rang 1**; das Nachlesen liefert sie mit zwei Nachbarabschnitten. Der Import übernimmt den letzten formal gültigen Entwurf mit **86 Befunden** aus `round_003/dossier_patch_applied.json`.

Diese Kontrolle belegt die Auffindbarkeit dieser Passage und die Lesbarkeit des historischen Bestands. Simulierte Tests und gespeicherte Quellen ersetzen keinen vollständigen Lauf mit echten Modellen oder die fachliche Abnahme einer fertigen Serie. Eine prozentuale Laufzeitverbesserung oder allgemeine Forschungsqualität lässt sich daraus nicht ableiten.
