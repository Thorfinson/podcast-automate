"""The fixed words of the files a podcast's listeners and readers get, in the podcast's content language (D-153).

``TopicBrief.language`` decides, never the Studio's interface language: these files are for the podcast's audience,
and a stage's outputs are hash-checked on resume (runner.outputs_valid), so a renderer gives the same bytes for the same
project, and the language is part of project_hash. German is the original wording, byte for byte as the files had it
before this table; English is its translation. A language without a table reads German, as every file did before.

The renderers: download and ZIP names (downloads), the companion kits of an episode and of the whole podcast with its
transcript (publish_kit), the series outline and the script run's show notes (script_artifacts), the recording's show
notes, listening sheet, README and transcript notice
(episode_audio, audio), the teaching plan and research_needed.md (teaching, script_pipeline), and the research files
a reader opens: the dossier and open_questions.md (research), the question ledger research_questions.md
(question_research), quality.md (research_quality) and the corpus probe's sentences (research_gap_probe.suffix).
Messages of the Studio, approval receipts and anything a prompt or a hash reads are not here; they keep their words.

The AI marking (D-154, EU AI Act Art. 50) is here as well: the transparency note the show notes, the listening sheet
and the companion kit's description end with, and the comment an exported MP3 carries next to its machine-readable
tags (audio.ai_marking).
"""
from __future__ import annotations

DEFAULT = "de-DE"

TEXT = {
    "de-DE": {
        # Download names (downloads.py).
        "episode": "Folge {number:02d}",
        "part_suffix": " - Teil {part:02d} von {parts:02d}",
        "all_episodes": "Alle Folgen",
        "some_episodes": "{finished} von {total} Folgen",
        "recording": "Aufnahme {number}",
        "companion_kit": "Begleitmaterial",
        # The companion kit (publish_kit.py).
        "kit_chapters": "Kapitel",
        "kit_sources": "Quellen",
        "kit_more": "Weitere Quellen: {count}",
        "kit_sources_title": "Quellen: {title}",
        "kit_sources_note": "Die Quellen der Rechercheergebnisse, auf die sich diese Folge stützt, in der Reihenfolge "
                            "ihrer ersten Verwendung.",
        "kit_no_sources": "Diese Folge stützt sich auf keine Quelle der Recherche.",
        # The podcast's own kit (publish_kit.build_podcast_kit) and its folder in the ZIP (downloads.py).
        "podcast_kit": "Begleitmaterial Podcast",
        "podcast_transcript_note": "Der freigegebene Text aller Folgen in Skriptreihenfolge. Die Zeitmarken der Kapitel "
                                   "stammen aus der gemessenen Montage der Aufnahme.",
        "podcast_untimed": "Noch nicht vertont: Die Kapitel stehen ohne Zeitmarken.",
        "podcast_sources_note": "Die Quellen der Rechercheergebnisse, auf die sich die Folgen stützen, in der "
                                "Reihenfolge ihrer ersten Verwendung; in Klammern die Folgen, die sie nutzen.",
        "podcast_no_sources": "Keine Folge stützt sich auf eine Quelle der Recherche.",
        "podcast_used_in": "(in {episodes})",
        # The series outline and the script run's show notes (script_artifacts.py).
        "outline_title": "Serienentwurf",
        "outline_planned": "Geplant: etwa {minutes:g} Minuten. ",
        "outline_checked": "Skript in diesem Lauf geprüft.",
        "outline_only_planned": "Bisher nur geplant.",
        "notes_title": "Quellen und Hinweise: {title}",
        "notes_chapters": "Kapitel",
        "notes_limits": "Grenzen und offene Vertiefungen",
        "notes_sources": "Quellen",
        "notes_traceability": "Nachvollziehbarkeit",
        "notes_runs": "Recherchelauf: `{research}`. Skriptlauf: `{script}`.",
        "notes_references": "Wissensreferenzen stehen im kanonischen Skript und führen über das Wissensmodell zu den "
                            "Quellenabschnitten.",
        # The recording's export (episode_audio.py, audio.py).
        "export_version": "Hörfassung mit {host_a} und {host_b}.",
        "export_voices": "Stimmen: {voice_a} ({host_a}) und {voice_b} ({host_b}).",
        "export_chapters": "Kapitel",
        "export_part": " (Teil {part})",
        "export_untimed": "Zeitmarken liegen für diese Fassung nicht vor.",
        "export_pronunciation": "Aussprache-Hinweise",
        "export_pronunciation_note": "Für diese Abschnitte wurde eine abweichende Sprechform vertont; der Text bleibt "
                                     "unverändert.",
        "export_measured": "Zeitmarken stammen aus der gemessenen Montage, nicht aus einer Schätzung.",
        "sheet_title": "Hörprüfung: {title}",
        "sheet_intro": "Beim Hören ausfüllen. Diese Spalten kann keine Prüfung im Programm ersetzen.",
        "sheet_part": "Teil",
        "sheet_time": "Zeit",
        "sheet_chapter": "Kapitel",
        "sheet_unclear": "Unklar",
        "sheet_attention": "Aufmerksamkeit verloren",
        "sheet_pronunciation": "Aussprache",
        "sheet_done": "Nach dem Hören im Studio ankreuzen, dass die Hörprüfung durchgeführt wurde.",
        "first_version": "Erste Audiofassung zur Hörprüfung.",
        "probe_notice": "Technische Hörprobe; keine recherchierte Podcastfolge.",
        "readme_listen": "[Folge anhören]({path}) – {minutes:.2f} Minuten",
        "readme_spoken_forms": "Abweichende Sprechformen",
        "readme_complete": "Der gesamte freigegebene Text ist in Skriptreihenfolge enthalten.",
        "readme_no_review": "Die technische Montage ersetzt keine Hörprüfung von Aussprache und Natürlichkeit.",
        # The teaching plan and its research questions (teaching.py).
        "plan_title": "Lehrplan: {episode}",
        "plan_start": "Ausgangspunkt",
        "plan_big_idea": "Große Idee",
        "plan_hook_question": "Leitfrage",
        "plan_first_answer": "Naheliegende erste Antwort",
        "plan_turning_point": "Wendepunkt",
        "plan_payoff": "Auflösung",
        "plan_callback": "Rückgriff auf den Anfang",
        "plan_dramaturgy": "Dramaturgie",
        "plan_opening": "Einstieg",
        "plan_stance": "Rolle der fragenden Stimme",
        "plan_arc": "Spannungsbogen",
        "plan_objectives": "Lernziele",
        "plan_reasoning": "Gedankengang",
        "plan_ending": "Kapitelende: {ending}",
        "plan_example": "Durchgearbeitetes Beispiel",
        "plan_misconception": "Mögliche Fehlvorstellung: ",
        "plan_limit": "Grenze: ",
        "plan_synthesis": "Synthese und Übertragung",
        "plan_research_limits": "Eingeordnete Forschungsgrenzen",
        "needed_title": "Recherche für die Erklärung ergänzen",
        "resolved_title": "Recherchefragen geklärt",
        "resolved_note": "Die Lehrplanung wurde mit den verfügbaren Quellen erneut geprüft und angenommen.",
        # The readable research dossier (research.render_dossier).
        "dossier_title": "Recherchedossier: {topic}",
        "dossier_run": "Recherchelauf: `{run_id}`",
        "dossier_read": "{sources} Quellen eingelesen; {failures} Abrufe/Importe fehlgeschlagen. ",
        "dossier_context": "Dem Modell wurden {sections} ausgewählte Textabschnitte vorgelegt. ",
        "dossier_checked": "Die Recherche wird gegen die vereinbarten Leitfragen geprüft; wissenschaftliche "
                           "Unsicherheiten bleiben ausdrücklich erkennbar.",
        "dossier_findings": "Befunde mit Quellenbezug",
        "dossier_contract": "Aussagetyp: {basis} / {relation}. Geltungsbereich: {scope}.",
        "dossier_qualification": "Einschränkung: {text}",
        "dossier_illustration": "Bild zum Mitdenken:",
        "dossier_illustration_limit": "Grenze des Bildes:",
        "dossier_page": ", Seite {page}",
        "dossier_evidence_check": "Automatisierte inhaltliche Belegprüfung dokumentiert. Dies ist keine unabhängige "
                                  "empirische Bestätigung; deren Status steht je Befund im evidence_report.json.",
        "dossier_synthesis": "Quellenübergreifende Vergleiche",
        "dossier_relation": "Bedingungen: {conditions}. Befunde: {findings}.",
        "dossier_coverage": "Abdeckung und Lücken",
        "dossier_coverage_findings": "Befunde: {findings}.",
        "dossier_none": "keine",
        "dossier_open": "Offene Fragen",
        "dossier_access": "Zugriffsprobleme",
        "dossier_no_access_problem": "Keine.",
        "dossier_sources": "Quellenverzeichnis",
        "dossier_no_author": "Autor nicht verifiziert",
        "dossier_no_date": "Datum unbekannt",
        "dossier_metadata": "Metadaten und Extraktionsgrenzen: siehe `models/source_index.yaml`.",
        # The question ledger research_questions.md (question_research.QuestionResearch._save).
        "rq_title": "Recherchefragen",
        "rq_closed": "{closed} von {total} Teilfragen geprüft abgeschlossen.",
        "rq_accepted": "{accepted} Teilfragen als Lücke ausdrücklich akzeptiert.",
        "rq_awaiting": "Der Rechercheplan wartet auf Freigabe; bis dahin wird kein weiterer Modellaufruf verbraucht.",
        "rq_budget": "Mindestens {minimum} weitere Modellaufrufe, davon {closing} für Dossier und Abschlussprüfung; "
                     "{remaining} verfügbar. Erfahrungsgemäß etwa {expected} Aufrufe ({per_task} je offener Teilfrage, "
                     "{source}). Zusätzliche Lese-, Such- und Korrekturschritte können mehr benötigen.",
        "rq_status": "Status: {status}",
        "rq_accepted_gap": " (akzeptierte Lücke)",
        "rq_criterion": "Abschlusskriterium: {criterion}",
        # open_questions.md (research.render_open_questions) and the corpus probe's sentences
        # (research_gap_probe.suffix, script_pipeline's research_needed.md).
        "open_title": "Offene Recherchefragen",
        "open_accepted": "Akzeptierte Lücke: {gap}",
        "probe_no_hits": "Korpusprobe: kein passender Abschnitt in den gespeicherten Quellen.",
        "probe_resolved": "Korpusprobe: in den gespeicherten Quellen beantwortet.",
        "probe_confirmed": "Korpusprobe: gelesen und bestätigt trotz Treffern in {references}.",
        "probe_unowned": "Korpusprobe: Treffer in {references}, die keine Folge nutzt.",
        "probe_unread": "Korpusprobe: ungelesene Treffer in {references}.",
        "probe_needed_title": "Gemeldete Lücken mit ungelesenen Korpustreffern",
        "probe_needed_why": "Die Korpusprobe hat zu dieser gemeldeten Lücke passende, noch ungelesene Abschnitte "
                            "gefunden: {references}. Diese Abschnitte müssen gelesen werden, bevor die Lücke behauptet "
                            "wird.",
        # quality.md (research_quality.render_quality); the criteria and probe states are catalogs (CATALOG_LABELS).
        "quality_title": "Recherchequalität",
        "quality_closed": "{closed} von {total} Leitfragen erfüllen alle Qualitätsmerkmale.",
        "quality_pending": "Die Quellenprüfung hat fehlende Belege gefunden. Zuerst wird gezielt nachrecherchiert; die "
                           "Bewertung aller Leitfragen wird danach erneuert. Die bisherigen Einzelbewertungen sind noch "
                           "kein Urteil über den ergänzten Entwurf.",
        "quality_residual": "Die Recherche wurde auf Wunsch der Redaktion mit dokumentierten Resteinwänden "
                            "abgeschlossen{note}. Die letzte Gesamtprüfung hatte noch Einwände; sie stehen unten und "
                            "gelten als offene Grenzen des Dossiers.",
        "quality_accepted": "Die Recherche wurde mit ausdrücklich akzeptierten Lücken abgeschlossen. Die betroffenen "
                            "Teilfragen und verbliebenen Einwände stehen unten; das Dossier behauptet für sie keine "
                            "Antwort.",
        "quality_noted": "Die Recherche wurde mit vermerkten Grenzen abgeschlossen: {closed} von {total} Leitfragen "
                         "erfüllen alle Merkmale. Was offen oder nur eingeschränkt belegt ist, steht unten als Grenze "
                         "(Quellengrenzen, unverändert vermerkte Einwände, Hinweise fürs Skript); dafür wird nicht "
                         "weiter recherchiert.",
        "quality_requirement_met": "Erfüllt",
        "quality_requirement_open": "Offen",
        "quality_criterion_met": "erfüllt",
        "quality_criterion_open": "offen",
        "quality_missing": "Noch benötigt: {gap}",
        "quality_noted_item": "Als Grenze vermerkt: {item}",
        "quality_source_limit": "Grenze der verfügbaren Quellen: wird nicht weiter recherchiert und ist im Skript zu "
                                "benennen",
        "quality_recorded_limit": "Als Grenze vermerkt: Keine Teilfrage dieser Leitfrage hat sich seit dem letzten "
                                  "Urteil geändert, und ihr Einwand wurde vermerkt, war strittig oder betrifft eine "
                                  "akzeptierte Lücke. Das Urteil bleibt, bis sich eine ihrer Antworten ändert; das "
                                  "Skript benennt die Grenze",
        "quality_accepted_task": "Akzeptierte Lücke: Teilfrage {task}",
        "quality_blocking": "Weitere offene Punkte",
        "quality_script_notes": "Hinweise fürs Skript",
        "quality_script_notes_intro": "Grenzen der verfügbaren Quellen und Punkte zu unveränderten Antworten, die die "
                                      "Folgebewertung nannte. Sie öffnen keine Recherche; das Skript benennt sie, wo es "
                                      "die betroffenen Aussagen verwendet.",
        "quality_single_group": "Befunde aus nur einer Forschungsgruppe",
        "quality_single_group_intro": "Beschreibend, nicht blockierend: Für diese Befunde stammen alle Belege aus einer "
                                      "bekannten Gruppe, oder die Gruppe ist unbekannt. Eine unabhängige Prüfung "
                                      "existiert für manche Aussagen von 2026 noch nicht; dann ist die Grenze zu "
                                      "benennen, nicht eine Quelle zu erzwingen.",
        "quality_unknown_group": "unbekannte Gruppe",
        "quality_probes": "Korpusprobe der Lücken",
        "quality_probes_intro": "Jede gemeldete Lücke wurde ohne Modellaufruf gegen die gespeicherten Abschnitte "
                                "geprüft. Ein Treffer widerlegt die Lücke nicht; er benennt einen Abschnitt, der gelesen "
                                "werden muss.",
        "quality_review_limits": "Einschränkungen der Prüfung",
        "quality_review_limits_intro": "Die unabhängige Antwortprüfung hat diese Teilfragen bestanden, einzelne "
                                       "Aussagen aber nur mit Einschränkung bestätigt. Sie gelten als Grenzen der "
                                       "Befunde, nicht als offene Recherche.",
        "quality_accepted_gaps": "Akzeptierte Lücken",
        "quality_disputed": "Strittige Prüfeinwände",
        "quality_disputed_intro": "Die Gesamtprüfung hat diesen früheren Einwänden widersprochen; die Redaktion hat "
                                  "entschieden.",
        "quality_side_reviewer": "dem Prüfer gefolgt, Einwand geschlossen",
        "quality_side_objection": "Einwand aufrechterhalten",
        "quality_objection": "Einwand: {text}",
        "quality_reviewer": "Prüfer: {text}",
        "quality_decision": "Entscheidung: {side}",
        "quality_noted_limits": "Als Grenzen vermerkte Vollständigkeitseinwände",
        "quality_noted_limits_intro": "Die geprüfte Antwort behandelt das jeweilige Kriterium mit belegten Befunden; die "
                                      "Gesamtprüfung hielt es für nicht ganz vollständig. Das steht hier als Grenze und "
                                      "wurde nicht erneut recherchiert.",
        "quality_after_reworks": "Einwände nach zwei Nachbesserungen",
        "quality_after_reworks_intro": "Diese Teilfragen wurden zweimal nachgebessert und behalten ihre zuletzt geprüfte "
                                       "Antwort. Spätere Einwände der Gesamtprüfung stehen hier als Grenzen; sie haben "
                                       "den Lauf nicht mehr angehalten.",
        "quality_rework_blocked": "Einwände, die eine Nachbesserung nicht schließen konnte",
        "quality_rework_blocked_intro": "Die Nachbesserung dieser Teilfragen fand keine neuen Belege und endete "
                                        "blockiert. Sie behalten ihre zuvor geprüfte Antwort; der Einwand steht hier "
                                        "als Grenze.",
        "quality_rework_note": " (Nachbesserung: {block})",
        "quality_revalidations": "Nachprüfungen nach geänderten Voraussetzungen",
        "quality_revalidations_intro": "Beschreibend, nicht blockierend: So oft wurde eine geprüfte Antwort erneut gegen "
                                       "eine nachgebesserte Voraussetzung geprüft. Diese Nachprüfungen zählen nicht als "
                                       "Nachbesserung.",
        "quality_residual_heading": "Verbliebene Prüfeinwände",
        "quality_residual_accepted": "Verbliebene Prüfeinwände zu akzeptierten Lücken",
        "quality_residual_noted": "Verbliebene Prüfeinwände, als Grenzen vermerkt oder strittig",
        # AI marking (D-154): the note in PRODUCT.md's roadmap, and the line that names what a model made.
        "transparency_heading": "Transparenzhinweis",
        "transparency": "Dieser Output ist eine quellengebundene Synthese. Er ersetzt keine fachliche, rechtliche, "
                        "medizinische oder wissenschaftliche Begutachtung. Unsichere oder widersprüchliche "
                        "Quellenlagen werden markiert.",
        "ai_notice": "KI-Hinweis: Das Skript dieser Folge wurde von einem Sprachmodell geschrieben und wird von "
                     "synthetischen Stimmen gesprochen.",
        "ai_notice_podcast": "KI-Hinweis: Die Skripte dieses Podcasts wurden von einem Sprachmodell geschrieben und "
                             "werden von synthetischen Stimmen gesprochen.",
        "mp3_comment": "KI-generiert: Skript von einem Sprachmodell geschrieben, Sprache mit synthetischen Stimmen "
                       "erzeugt.",
        "mp3_comment_probe": "KI-generiert: Sprache mit synthetischen Stimmen erzeugt.",
    },
    "en-US": {
        "episode": "Episode {number:02d}",
        "part_suffix": " - Part {part:02d} of {parts:02d}",
        "all_episodes": "All episodes",
        "some_episodes": "{finished} of {total} episodes",
        "recording": "Recording {number}",
        "companion_kit": "Companion kit",
        "kit_chapters": "Chapters",
        "kit_sources": "Sources",
        "kit_more": "Further sources: {count}",
        "kit_sources_title": "Sources: {title}",
        "kit_sources_note": "The sources of the research findings this episode relies on, in the order of their "
                            "first use.",
        "kit_no_sources": "This episode relies on no source of the research.",
        "podcast_kit": "Podcast companion kit",
        "podcast_transcript_note": "The approved text of every episode in script order. The chapter timestamps come "
                                   "from the measured assembly of the recording.",
        "podcast_untimed": "Not recorded yet: the chapters have no timestamps.",
        "podcast_sources_note": "The sources of the research findings the episodes rely on, in the order of their "
                                "first use; in brackets the episodes that use them.",
        "podcast_no_sources": "No episode relies on a source of the research.",
        "podcast_used_in": "(in {episodes})",
        "outline_title": "Series outline",
        "outline_planned": "Planned: about {minutes:g} minutes. ",
        "outline_checked": "Script checked in this run.",
        "outline_only_planned": "Planned only so far.",
        "notes_title": "Sources and notes: {title}",
        "notes_chapters": "Chapters",
        "notes_limits": "Limits and open deep dives",
        "notes_sources": "Sources",
        "notes_traceability": "Traceability",
        "notes_runs": "Research run: `{research}`. Script run: `{script}`.",
        "notes_references": "Knowledge references are in the canonical script and lead through the knowledge model to "
                            "the source sections.",
        "export_version": "Audio version with {host_a} and {host_b}.",
        "export_voices": "Voices: {voice_a} ({host_a}) and {voice_b} ({host_b}).",
        "export_chapters": "Chapters",
        "export_part": " (part {part})",
        "export_untimed": "No timestamps are available for this version.",
        "export_pronunciation": "Pronunciation notes",
        "export_pronunciation_note": "These segments were recorded with a different spoken form; the text is "
                                     "unchanged.",
        "export_measured": "Timestamps come from the measured assembly, not from an estimate.",
        "sheet_title": "Listening review: {title}",
        "sheet_intro": "Fill in while listening. No check in the program can replace these columns.",
        "sheet_part": "Part",
        "sheet_time": "Time",
        "sheet_chapter": "Chapter",
        "sheet_unclear": "Unclear",
        "sheet_attention": "Attention lost",
        "sheet_pronunciation": "Pronunciation",
        "sheet_done": "After listening, tick in the Studio that the listening review was done.",
        "first_version": "First audio version for listening review.",
        "probe_notice": "Technical voice sample; not a researched podcast episode.",
        "readme_listen": "[Listen to the episode]({path}) – {minutes:.2f} minutes",
        "readme_spoken_forms": "Deviating spoken forms",
        "readme_complete": "The whole approved text is included in script order.",
        "readme_no_review": "The technical assembly does not replace a listening review of pronunciation and "
                            "naturalness.",
        "plan_title": "Teaching plan: {episode}",
        "plan_start": "Starting point",
        "plan_big_idea": "Big idea",
        "plan_hook_question": "Guiding question",
        "plan_first_answer": "Obvious first answer",
        "plan_turning_point": "Turning point",
        "plan_payoff": "Payoff",
        "plan_callback": "Callback to the opening",
        "plan_dramaturgy": "Dramaturgy",
        "plan_opening": "Opening",
        "plan_stance": "Role of the questioning voice",
        "plan_arc": "Narrative arc",
        "plan_objectives": "Learning objectives",
        "plan_reasoning": "Line of reasoning",
        "plan_ending": "Chapter ending: {ending}",
        "plan_example": "Worked example",
        "plan_misconception": "Possible misconception: ",
        "plan_limit": "Limit: ",
        "plan_synthesis": "Synthesis and transfer",
        "plan_research_limits": "Research limits taken into account",
        "needed_title": "Research to add for the explanation",
        "resolved_title": "Research questions resolved",
        "resolved_note": "The teaching plan was checked again with the available sources and accepted.",
        "dossier_title": "Research dossier: {topic}",
        "dossier_run": "Research run: `{run_id}`",
        "dossier_read": "{sources} sources read; {failures} retrievals or imports failed. ",
        "dossier_context": "The model was given {sections} selected text sections. ",
        "dossier_checked": "The research is checked against the agreed guiding questions; scientific uncertainties "
                           "stay explicitly visible.",
        "dossier_findings": "Findings with source references",
        "dossier_contract": "Claim type: {basis} / {relation}. Scope: {scope}.",
        "dossier_qualification": "Qualification: {text}",
        "dossier_illustration": "Illustration:",
        "dossier_illustration_limit": "Limit of the illustration:",
        "dossier_page": ", page {page}",
        "dossier_evidence_check": "Automated content check of the evidence documented. This is not an independent "
                                  "empirical confirmation; its status per finding is in evidence_report.json.",
        "dossier_synthesis": "Comparisons across sources",
        "dossier_relation": "Conditions: {conditions}. Findings: {findings}.",
        "dossier_coverage": "Coverage and gaps",
        "dossier_coverage_findings": "Findings: {findings}.",
        "dossier_none": "none",
        "dossier_open": "Open questions",
        "dossier_access": "Access problems",
        "dossier_no_access_problem": "None.",
        "dossier_sources": "Sources",
        "dossier_no_author": "Author not verified",
        "dossier_no_date": "Date unknown",
        "dossier_metadata": "Metadata and extraction limits: see `models/source_index.yaml`.",
        "rq_title": "Research questions",
        "rq_closed": "{closed} of {total} sub-questions checked and closed.",
        "rq_accepted": "{accepted} sub-questions explicitly accepted as gaps.",
        "rq_awaiting": "The research plan awaits approval; until then no further model call is spent.",
        "rq_budget": "At least {minimum} more model calls, {closing} of them for the dossier and the final check; "
                     "{remaining} available. From experience about {expected} calls ({per_task} per open sub-question, "
                     "{source}). Additional reading, search and correction steps can need more.",
        "rq_status": "Status: {status}",
        "rq_accepted_gap": " (accepted gap)",
        "rq_criterion": "Completion criterion: {criterion}",
        "open_title": "Open research questions",
        "open_accepted": "Accepted gap: {gap}",
        "probe_no_hits": "Corpus probe: no matching section in the stored sources.",
        "probe_resolved": "Corpus probe: answered in the stored sources.",
        "probe_confirmed": "Corpus probe: read and confirmed despite hits in {references}.",
        "probe_unowned": "Corpus probe: hits in {references}, which no episode uses.",
        "probe_unread": "Corpus probe: unread hits in {references}.",
        "probe_needed_title": "Reported gaps with unread corpus hits",
        "probe_needed_why": "The corpus probe found matching sections for this reported gap that are still unread: "
                            "{references}. These sections must be read before the gap is claimed.",
        "quality_title": "Research quality",
        "quality_closed": "{closed} of {total} guiding questions meet every quality criterion.",
        "quality_pending": "The source check found missing evidence. Targeted research comes first; the assessment of "
                           "all guiding questions is renewed afterwards. The individual assessments so far are no "
                           "verdict on the supplemented draft yet.",
        "quality_residual": "The research was closed at the editors' request with documented residual objections{note}. "
                            "The last overall check still had objections; they are listed below and count as open "
                            "limits of the dossier.",
        "quality_accepted": "The research was closed with explicitly accepted gaps. The affected sub-questions and "
                            "remaining objections are listed below; the dossier claims no answer for them.",
        "quality_noted": "The research was closed with noted limits: {closed} of {total} guiding questions meet every "
                         "criterion. What is open or only partly supported is listed below as a limit (source limits, "
                         "objections noted unchanged, notes for the script); no further research is done for it.",
        "quality_requirement_met": "Met",
        "quality_requirement_open": "Open",
        "quality_criterion_met": "met",
        "quality_criterion_open": "open",
        "quality_missing": "Still needed: {gap}",
        "quality_noted_item": "Noted as a limit: {item}",
        "quality_source_limit": "Limit of the available sources: not researched further, to be named in the script",
        "quality_recorded_limit": "Noted as a limit: no sub-question of this guiding question has changed since the "
                                  "last verdict, and its objection was noted, disputed or concerns an accepted gap. The "
                                  "verdict stands until one of its answers changes; the script names the limit",
        "quality_accepted_task": "Accepted gap: sub-question {task}",
        "quality_blocking": "Further open points",
        "quality_script_notes": "Notes for the script",
        "quality_script_notes_intro": "Limits of the available sources and points on unchanged answers that the "
                                      "follow-up assessment named. They open no research; the script names them where "
                                      "it uses the statements concerned.",
        "quality_single_group": "Findings from a single research group",
        "quality_single_group_intro": "Descriptive, not blocking: for these findings all evidence comes from one known "
                                      "group, or the group is unknown. For some statements from 2026 no independent "
                                      "check exists yet; then the limit is to be named, not a source forced.",
        "quality_unknown_group": "unknown group",
        "quality_probes": "Corpus probe of the gaps",
        "quality_probes_intro": "Every reported gap was checked against the stored sections without a model call. A hit "
                                "does not refute the gap; it names a section that has to be read.",
        "quality_review_limits": "Limitations of the review",
        "quality_review_limits_intro": "These sub-questions passed the independent answer review, but some statements "
                                       "were confirmed only with a qualification. They count as limits of the "
                                       "findings, not as open research.",
        "quality_accepted_gaps": "Accepted gaps",
        "quality_disputed": "Disputed review objections",
        "quality_disputed_intro": "The overall check contradicted these earlier objections; the editors decided.",
        "quality_side_reviewer": "followed the reviewer, objection closed",
        "quality_side_objection": "objection upheld",
        "quality_objection": "Objection: {text}",
        "quality_reviewer": "Reviewer: {text}",
        "quality_decision": "Decision: {side}",
        "quality_noted_limits": "Completeness objections noted as limits",
        "quality_noted_limits_intro": "The checked answer covers the criterion with supported findings; the overall "
                                      "check considered it not quite complete. It stands here as a limit and was not "
                                      "researched again.",
        "quality_after_reworks": "Objections after two reworks",
        "quality_after_reworks_intro": "These sub-questions were reworked twice and keep their last checked answer. "
                                       "Later objections of the overall check stand here as limits; they no longer "
                                       "stopped the run.",
        "quality_rework_blocked": "Objections a rework could not close",
        "quality_rework_blocked_intro": "The rework of these sub-questions found no new evidence and ended blocked. They "
                                        "keep their previously checked answer; the objection stands here as a limit.",
        "quality_rework_note": " (rework: {block})",
        "quality_revalidations": "Re-checks after changed prerequisites",
        "quality_revalidations_intro": "Descriptive, not blocking: this is how often a checked answer was checked again "
                                       "against a reworked prerequisite. These re-checks do not count as a rework.",
        "quality_residual_heading": "Remaining review objections",
        "quality_residual_accepted": "Remaining review objections on accepted gaps",
        "quality_residual_noted": "Remaining review objections, noted as limits or disputed",
        "transparency_heading": "Transparency note",
        "transparency": "This output is a source-bound synthesis. It does not replace a subject-matter, legal, medical "
                        "or scientific assessment. Uncertain or contradictory source situations are marked.",
        "ai_notice": "AI notice: the script of this episode was written by a language model and is spoken by "
                     "synthetic voices.",
        "ai_notice_podcast": "AI notice: the scripts of this podcast were written by a language model and are spoken "
                             "by synthetic voices.",
        "mp3_comment": "AI-generated: script written by a language model, speech made with synthetic voices.",
        "mp3_comment_probe": "AI-generated: speech made with synthetic voices.",
    },
}

# Labels of catalogs whose German words live next to the code that also hands them to a prompt or a report, so they
# stay there and a German file reads them as before: the storytelling devices of a teaching plan (dramaturgy.py), the
# research quality criteria (research_quality.CRITERIA, which the quality report records), the corpus probe states
# (research_quality.PROBE_LABELS) and where a call estimate comes from (question_budget.SOURCE_LABELS, which the plan
# gate's message uses too). Here only the other languages' words.
CATALOG_LABELS = {
    "en-US": {
        "criterion": {"direct_answer": "Guiding question fully answered",
                      "explanation": "Foundations, mechanism and a comprehensible example",
                      "evidence": "Content evidence from read, independent sources",
                      "cross_check": "Opposing positions and independent checks considered",
                      "boundaries": "Limits of validity, uncertainty and connections explained"},
        "probe_status": {"no_hits": "no matching section found", "hits_unread": "hits not read yet",
                         "hits_unowned": "hits in sources no episode uses",
                         "hits_read_confirmed": "hits read, gap confirmed", "resolved": "answered in the sources"},
        "call_source": {"run": "measured in this run", "project": "the project's experience",
                        "default": "default value"},
        "dramaturgy": {"mystery": "Mystery", "discovery": "Story of a discovery", "in_medias_res": "In medias res",
                       "debate": "Debate", "thought_experiment": "Thought experiment",
                       "myth_check": "Myth versus finding", "case_autopsy": "Case analysis",
                       "build_along": "Build along"},
        "opening": {"scene": "Scene", "number": "Number", "question": "Question", "quote": "Quote",
                    "belief": "Belief", "anecdote": "Anecdote", "problem": "Problem"},
        "partner_stance": {"skeptic": "Skeptical", "enthusiast": "Enthusiastic", "devils_advocate": "Devil's advocate",
                           "learner": "Thinking along", "practice": "From practice"},
        "ending": {"open_question": "Open question", "recap": "Interim summary", "surprise": "Surprise",
                   "contradiction": "Contradiction", "foreshadow": "Foreshadowing", "reflection": "Reflection",
                   "conclusion": "Conclusion"},
    },
}


def words(language):
    """The table of ``language``; German for a language without one."""
    return TEXT.get(language, TEXT[DEFAULT])


def text(language, key, **values):
    """One entry of ``language``'s table with its ``{fields}`` filled from ``values``."""
    entry = words(language)[key]
    return entry.format(**values) if values else entry


def catalog_label(language, catalog, key):
    """The label of ``key`` in ``catalog`` (see CATALOG_LABELS) in ``language``, or None where the catalog's own module
    gives it (German, the original), so the caller reads it there."""
    return CATALOG_LABELS.get(language, {}).get(catalog, {}).get(key)


def transparency_lines(language, *, podcast=False):
    """The note a Markdown export ends with (D-154): a heading, the source-bound synthesis note of PRODUCT.md's
    roadmap and the line that names what was made with AI, each followed by a blank line. ``podcast`` names the
    scripts of the whole podcast instead of this episode's, for the podcast's own kit."""
    table = words(language)
    return [f"## {table['transparency_heading']}", "", table["transparency"], "",
            table["ai_notice_podcast" if podcast else "ai_notice"], ""]


def transparency_text(language, *, podcast=False):
    """The same note as plain text, for the companion kit's description."""
    table = words(language)
    return table["transparency"] + "\n" + table["ai_notice_podcast" if podcast else "ai_notice"]


def mp3_comment(language, purpose):
    """The ID3 comment of an exported MP3 (D-154): synthetic speech, and for an episode a script a model wrote; a
    technical voice sample reads a fixed text."""
    return text(language, "mp3_comment_probe" if purpose == "technical_probe" else "mp3_comment")
