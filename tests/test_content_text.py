"""Exports follow the podcast's content language (D-153) and carry the transparency note (D-154).

The German goldens below were captured from the renderers before content_text.py existed (2026-10-07): a German
project writes the same files and download names as before. The files D-154 marks end with the transparency note as
their only change, asserted as a suffix of their golden (MARKED_DE)."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.evidence_models import ClaimContract, SynthesisRelation
from podcast_automate.models import Chapter, EpisodeScript, Segment, TopicBrief
from podcast_automate.research_models import (Evidence, Finding, QuestionCoverage, ResearchDiscovery, ResearchDossier,
                                              ResearchQuestion, SourceCandidate, SourceDocument, SourceIndex,
                                              SourceSection)
from podcast_automate.scripting import run_script
from podcast_automate.teaching import ResearchGap, TeachingPlan, TeachingPlanReview
from tests import script_fixtures as fixtures
from tests.teaching_fixtures import teaching_response


def episode_script():
    return EpisodeScript(episode_id="ep_002", title="Wie ein Modell vergleicht", purpose="deep_dive",
        chapters=[Chapter(chapter_id="c_one", title="Erstes Kapitel"), Chapter(chapter_id="c_two", title="Zweites Kapitel")],
        segments=[Segment(segment_id="seg_001", scene_id="c_one", chapter_id="c_one", speaker_id="host_a",
                          text="Was vergleicht das Modell?"),
                  Segment(segment_id="seg_002", scene_id="c_two", chapter_id="c_two", speaker_id="host_b",
                          text="Zwei Möglichkeiten.")])


PARTED = [{"part": 1, "timestamp": "0:00", "title": "Erstes Kapitel"},
          {"part": 2, "timestamp": "0:00", "title": "Zweites Kapitel"}]
ONE_PART = [{"part": 1, "timestamp": "0:00", "title": "Erstes Kapitel"},
            {"part": 1, "timestamp": "12:30", "title": "Zweites Kapitel"}]
VOICES = SimpleNamespace(voices={"host_a": "Erinome", "host_b": "Sadachbia"})
OVERRIDES = {"seg_002": "Zwei Moeglichkeiten.", "seg_001": "Was vergleicht das Modell"}
HOSTS = {"host_a": "Mara", "host_b": "Jonas"}


def told_design():
    """The fixture design with every optional part: the arc, the storytelling devices and the chapter endings."""
    saved = teaching_response(json.dumps({"episode": fixtures.example_plan().episodes[0].model_dump()}),
                              TeachingPlan).model_dump()
    return TeachingPlan.model_validate({
        **saved, "big_idea": "A score decides between candidates.", "hook_question": "Why would lower be better?",
        "first_answer": "A higher score sounds better.", "turning_point": "The energy convention turns it around.",
        "turning_finding_ids": ["f_energy"], "payoff": "Lower energy means a better fit.",
        "callback": "The two candidates from the opening.", "dramaturgy": "discovery", "opening": "anecdote",
        "partner_stance": "skeptic", "scenes": [{**scene, "ending": "conclusion"} for scene in saved["scenes"]]})


def kit_rows():
    return [{"title": "Attention is All you Need", "authors": ["Ashish Vaswani", "Noam Shazeer"], "year": "2017",
             "url": "https://arxiv.org/abs/1706.03762"},
            {"title": "Ein Buch [Band 2]?", "authors": [], "year": "", "url": ""}]


def kit_chapters():
    return [{"chapter_id": "c_one", "title": "Anfang", "start_seconds": 0.0, "timestamp": "00:00"},
            {"chapter_id": "c_two", "title": "Ende", "start_seconds": 120.0, "timestamp": "02:00"}]


KIT_LONG = "Eine kurze Beschreibung der Folge.\n\nEin zweiter Absatz."


def document(source_id, title, *, final_url="", authors=(), published="", page=None):
    return SourceDocument(id=source_id, type="html", title=title, authors=list(authors), published_date=published,
                          imported_at="2026-10-06T00:00:00+00:00", url=final_url, final_url=final_url,
                          reliability_note="", uncertainties=[], raw_path=f"sources/raw/run_x/{source_id}.html",
                          raw_hash="0" * 64, text_hash="0" * 64,
                          sections=[SourceSection(id="sec_0001", text="Text.", page=page)])


def dossier_inputs():
    """A dossier that passes every branch of the readable rendering."""
    index = SourceIndex(sources=[
        document("src_a", "Ein [Fach] Artikel", final_url="https://example.org/a", authors=["A. Autor", "B. Autorin"],
                 published="2020", page=12),
        document("src_b", "Notizen ohne Adresse")], failures=[{"source": "https://example.org/x", "reason": "HTTP 403"}])
    discovery = ResearchDiscovery(topic="Testthema", questions=[
        ResearchQuestion(id="q_one", question="Was ist Energie?", search_query="energy"),
        ResearchQuestion(id="q_two", question="Wie wird gelernt?", search_query="learning"),
        ResearchQuestion(id="q_three", question="Was bleibt offen?", search_query="open")],
        candidates=[SourceCandidate(url="https://example.org/a", title="A", authors=[], published_date="",
                                    rationale="Fixture", primary_source=True)], limitations=[])
    dossier = ResearchDossier(topic="Testthema", scope_note="Ein begrenztes Dossier.", findings=[
        Finding(id="f_one", kind="definition", statement="Konfigurationen erhalten Energien.",
                claim_contract=ClaimContract(basis="source_definition", relation="definition",
                                             scope=["Beispiel", "Test"], qualifications=["Nur im Beispiel."],
                                             quantities=[]),
                illustration="Eine Waage.", illustration_limit="Keine echte Waage.",
                evidence=[Evidence(reference="src_a#sec_0001", excerpt="Models assign an energy"),
                          Evidence(reference="src_b#sec_0001", excerpt="Text")]),
        Finding(id="f_two", kind="claim", statement="Lernen und Schließen sind getrennt.",
                evidence=[Evidence(reference="src_b#sec_0001", excerpt="Text")])],
        coverage=[QuestionCoverage(question_id="q_one", status="answered", finding_ids=["f_one"], gap=""),
                  QuestionCoverage(question_id="q_two", status="partial", finding_ids=["f_two"], gap="Teilweise."),
                  QuestionCoverage(question_id="q_three", status="unanswered", finding_ids=[], gap="Offen.")],
        open_questions=["Wie genau?"], evidence_version="evidence.v1",
        synthesis=[SynthesisRelation(id="rel_one", finding_ids=["f_one", "f_two"], dimension="Lernen",
                                     relation="conditional_difference", conditions="Im Test",
                                     evidence_refs=["src_a#sec_0001"], resolution="resolved",
                                     explanation="Beide gelten.", basis="source_comparison")])
    context = [{"sections": [1, 2]}, {"sections": [3]}]
    return dossier, discovery, index, context


def run_teaching_gaps(folder):
    """build_teaching_plan as a run that first needs research and then accepts the plan with one research limit:
    returns research_needed.md as written while blocked, then plan.md and research_needed.md after acceptance."""
    from podcast_automate.teaching import build_teaching_plan
    config, entry = TopicBrief(topic="Test topic"), fixtures.example_plan().episodes[0]
    gap = ResearchGap(question="Wie wird gelernt?", why_needed="Der Mechanismus fehlt.")
    required = {"value": True}

    def invoke(prompt, schema, version):
        result = teaching_response(prompt, schema)
        if schema is TeachingPlan:
            result.research_gaps = [gap]
        if schema is TeachingPlanReview:
            result = TeachingPlanReview(issues=[], gap_assessments=[{
                "gap": gap.question, "required_for_objective": required["value"],
                "reason": "Für das Lernziel nicht nötig."}],
                research_gaps=[ResearchGap(question="Woher kommen die Werte?", why_needed="Belege fehlen.")]
                if required["value"] else [])
        return result
    empty = SimpleNamespace(findings=[], synthesis=[])
    try:
        build_teaching_plan(config, entry, empty, [], invoke, folder)
        raise AssertionError("the first build needs research")
    except AppError as exc:
        assert exc.code == "teaching_research_required", exc.code
    needed = (folder / "research_needed.md").read_text(encoding="utf-8")
    (folder / "checkpoint.json").unlink()
    required["value"] = False
    build_teaching_plan(config, entry, empty, [], invoke, folder)
    return needed, (folder / "plan.md").read_text(encoding="utf-8"), (folder / "research_needed.md").read_text(encoding="utf-8")


def quality_report(**flags):
    """A research quality report that passes every branch of render_quality; ``flags`` choose the closing heading."""
    from podcast_automate.research_quality import CRITERIA
    return {
        "closed": 1, "total": 2, "assessment_status": "pending_after_source_review", "residual_note": "Notiz",
        "passed_with_residual_objections": True, "passed_with_accepted_gaps": True, "passed_with_noted_limits": True,
        **flags,
        "requirements": [
            {"question": "Frage A", "passed": True, "reason": "Grund A", **dict.fromkeys(CRITERIA, True), "missing": [],
             "noted": ["Vermerk"], "source_limit": True, "recorded_limit": True, "accepted_gap_tasks": ["task_a"]},
            {"question": "Frage B", "passed": False, "reason": "Grund B", **dict.fromkeys(CRITERIA, False),
             "missing": ["Beleg fehlt"]}],
        "blocking_gaps": ["Lücke X"], "script_notes": ["Hinweis eins"],
        "advisories": {"single_group_findings": [{"finding_id": "f_a", "research_group": "", "source_ids": ["src_a"]},
                                                 {"finding_id": "f_b", "research_group": "Gruppe B",
                                                  "source_ids": ["src_b", "src_c"]}]},
        "gap_probes": [{"text": "Lücke X", "status": "hits_unread", "hits": [{"reference": "src_a#sec_0001"}]},
                       {"text": "Lücke Y", "status": "no_hits", "hits": []},
                       {"text": "Lücke Z", "status": "something_new", "hits": []}],
        "review_limitations": [{"question": "Frage A", "limitations": [{"text": "Nur eingeschränkt."}]}],
        "accepted_gaps": [{"task_id": "task_a", "question": "Teilfrage A", "reason": "Kein Zugang"},
                          {"task_id": "task_b", "question": "", "reason": ""}],
        "accepted_coverage_gaps": ["Abdeckungslücke"],
        "disputed_objections": [{"objection_id": "obj_1", "objection": {"reason": "Einwand eins"},
                                 "review": {"reason": "Prüfer eins"}, "decision": "reviewer", "note": "Notiz"},
                                {"objection_id": "obj_2", "review": {"reason": "Prüfer zwei"}, "decision": "objection"}],
        "noted_limits": ["Grenze eins"],
        "noted_after_reworks": [{"task_id": "task_c", "objection": "Einwand C", "basis": "two_reworks"},
                                {"task_id": "task_d", "objection": "Einwand D", "basis": "rework_blocked",
                                 "rework_block": "keine Belege"},
                                {"task_id": "task_e", "objection": "Einwand E", "basis": "rework_blocked"}],
        "revalidations": [{"question": "Teilfrage F", "task_id": "task_f", "count": 2}, {"task_id": "task_g", "count": 1}],
        "residual_objections": ["Resteinwand"]}


QUALITY_VARIANTS = {"quality_residual": {}, "quality_accepted": {"passed_with_residual_objections": False,
                                                                  "passed_with_noted_limits": False},
                    "quality_noted": {"passed_with_residual_objections": False}}


def probe_rows():
    hits = [{"reference": "src_a#sec_0001"}, {"reference": "src_b#sec_0002"}]
    return [{"gap_id": f"gap_{status}", "text": f"Lücke {status}", "status": status, "hits": [] if status == "no_hits" else hits}
            for status in ("no_hits", "resolved", "hits_read_confirmed", "hits_unowned", "hits_unread")]


def ledger_inputs(phase="awaiting_plan_approval", accepted=1, source="project"):
    """What QuestionResearch._save renders into research_questions.md: the public ledger, the budget projection and
    the reader's lookup of a cited section (with and without a page)."""
    public = {"closed": 1, "total": 3, "accepted": accepted, "questions": [
        {"question": "Was ist Energie?", "status": "verified", "accepted_gap": False, "answer": "Eine Zahl.",
         "activity": "fertig", "acceptance": ["Definition", "Beispiel"], "reason": "",
         "findings": [{"statement": "Energie ordnet.", "evidence": [{"reference": "src_a#sec_0001"},
                                                                   {"reference": "src_b#sec_0001"}]}]},
        {"question": "Wie wird gelernt?", "status": "blocked", "accepted_gap": True, "answer": "",
         "activity": "Suche läuft", "acceptance": [], "reason": "Kein Zugang zur Quelle.", "findings": []}]}
    budget = {"minimum_remaining_calls": 7, "closing_calls": 4, "remaining": 40, "expected_remaining_calls": 18,
              "expected_calls_per_task": 9, "expected_calls_source": source}
    lookup = {"src_a#sec_0001": (SimpleNamespace(title="Ein [Fach] Artikel", final_url="https://example.org/a"), None,
                                 SimpleNamespace(page=12)),
              "src_b#sec_0001": (SimpleNamespace(title="Notizen", final_url="https://example.org/b"), None,
                                 SimpleNamespace(page=None))}
    return public, budget, {"phase": phase}, lookup


# The corpus probe of two of dossier_inputs' gaps, as the quality report records it for open_questions.md.
OPEN_PROBES = [{"text": "Wie genau?", "status": "no_hits", "hits": []},
               {"text": "Offen.", "status": "hits_unread", "hits": [{"reference": "src_a#sec_0001"}]}]
# Gaps with unread corpus hits as script_pipeline.route_probe_gaps hands them on; the German why_needed is what the
# supplementary research reads, unchanged.
PROBE_QUESTIONS = [
    {"question": "Lücke A", "kind": "evidence", "references": ["src_a#sec_0001"],
     "why_needed": "Die Korpusprobe hat zu dieser gemeldeten Lücke passende, noch ungelesene Abschnitte gefunden: "
                   "src_a#sec_0001. Diese Abschnitte müssen gelesen werden, bevor die Lücke behauptet wird."},
    {"question": "Lücke B", "kind": "evidence", "references": ["src_b#sec_0002"],
     "why_needed": "Die Korpusprobe hat zu dieser gemeldeten Lücke passende, noch ungelesene Abschnitte gefunden: "
                   "src_b#sec_0002. Diese Abschnitte müssen gelesen werden, bevor die Lücke behauptet wird."}]


def render_ledger(folder, language="de-DE", **options):
    """research_questions.md as QuestionResearch._save writes it, with the ledger and budget given."""
    from podcast_automate.question_research import QuestionResearch
    public, budget, state, lookup = ledger_inputs(**options)
    owner = SimpleNamespace(state=state, attempts=None, folder=folder, work=folder, root=folder, index=None,
                            limits=lambda: None, reader=SimpleNamespace(lookup=lookup),
                            config=TopicBrief(topic="Test", language=language))
    with patch("podcast_automate.question_research.budget_projection", return_value=budget), \
            patch("podcast_automate.question_research.public_ledger", return_value=public):
        QuestionResearch._save(owner, None, None)
    return (folder / "research_questions.md").read_text(encoding="utf-8")


def published_files(fixture, root):
    """series_outline.md, the episode's show_notes.md and teaching_plan.md of a fixture script run, run ids named."""
    with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
        run = run_script(root)
    assert run.status == "completed", run.status
    research = json.loads((root / "research/latest.json").read_text(encoding="utf-8"))["run_id"]
    texts = {}
    for name in ("research/series_outline.md", "episodes/ep_001/show_notes.md", "episodes/ep_001/teaching_plan.md"):
        text = (root / name).read_text(encoding="utf-8")
        texts[name] = text.replace(research, "<research_run>").replace(run.run_id, "<script_run>")
    return texts


GOLDEN = {
    'file_single': 'Transformer - Folge 03 - Übersetzung Wie rechnet Aufmerksamkeit.mp3',
    'file_part': 'Transformer - Folge 03 - Übersetzung Wie rechnet Aufmerksamkeit - Teil 02 von 03.mp3',
    'archive_part': 'Folge 03 - Übersetzung Wie rechnet Aufmerksamkeit - Teil 02 von 03.mp3',
    'archive_long': 'Folge 03 - Lang Lang Lang Lang Lang Lang Lang Lang Lang Lang Lang Lang Lang Lang Lang.mp3',
    'zip_all': 'Transformer - Alle Folgen.zip',
    'zip_some': 'Transformer - 1 von 3 Folgen.zip',
    'notes_parted': (
        '# Wie ein Modell vergleicht\n'
        '\n'
        'Hörfassung mit Mara und Jonas.\n'
        'Stimmen: Erinome (Mara) und Sadachbia (Jonas).\n'
        '\n'
        '## Kapitel\n'
        '\n'
        '- 0:00 Erstes Kapitel\n'
        '- 0:00 Zweites Kapitel (Teil 2)\n'
        '\n'
        '## Aussprache-Hinweise\n'
        '\n'
        'Für diese Abschnitte wurde eine abweichende Sprechform vertont; der Text bleibt unverändert.\n'
        '\n'
        '- `seg_001`: Was vergleicht das Modell\n'
        '- `seg_002`: Zwei Moeglichkeiten.\n'
        '\n'
        'Zeitmarken stammen aus der gemessenen Montage, nicht aus einer Schätzung.\n'
    ),
    'notes_untimed': (
        '# Wie ein Modell vergleicht\n'
        '\n'
        'Hörfassung mit Host A und Host B.\n'
        'Stimmen: Erinome (Host A) und Sadachbia (Host B).\n'
        '\n'
        '## Kapitel\n'
        '\n'
        '- Zeitmarken liegen für diese Fassung nicht vor.\n'
        '\n'
        'Zeitmarken stammen aus der gemessenen Montage, nicht aus einer Schätzung.\n'
    ),
    'sheet_one_part': (
        '# Hörprüfung: Wie ein Modell vergleicht\n'
        '\n'
        'Beim Hören ausfüllen. Diese Spalten kann keine Prüfung im Programm ersetzen.\n'
        '\n'
        '| Zeit | Kapitel | Unklar | Aufmerksamkeit verloren | Aussprache |\n'
        '| --- | --- | --- | --- | --- |\n'
        '| 0:00 | Erstes Kapitel | | | |\n'
        '| 12:30 | Zweites Kapitel | | | |\n'
        '\n'
        'Nach dem Hören im Studio ankreuzen, dass die Hörprüfung durchgeführt wurde.\n'
    ),
    'sheet_parted': (
        '# Hörprüfung: Wie ein Modell vergleicht\n'
        '\n'
        'Beim Hören ausfüllen. Diese Spalten kann keine Prüfung im Programm ersetzen.\n'
        '\n'
        '| Teil | Zeit | Kapitel | Unklar | Aufmerksamkeit verloren | Aussprache |\n'
        '| --- | --- | --- | --- | --- | --- |\n'
        '| 1 | 0:00 | Erstes Kapitel | | | |\n'
        '| 2 | 0:00 | Zweites Kapitel | | | |\n'
        '\n'
        'Nach dem Hören im Studio ankreuzen, dass die Hörprüfung durchgeführt wurde.\n'
    ),
    'sheet_untimed': (
        '# Hörprüfung: Wie ein Modell vergleicht\n'
        '\n'
        'Beim Hören ausfüllen. Diese Spalten kann keine Prüfung im Programm ersetzen.\n'
        '\n'
        '| Zeit | Kapitel | Unklar | Aufmerksamkeit verloren | Aussprache |\n'
        '| --- | --- | --- | --- | --- |\n'
        '| 0:00 | Erstes Kapitel | | | |\n'
        '| 0:00 | Zweites Kapitel | | | |\n'
        '\n'
        'Nach dem Hören im Studio ankreuzen, dass die Hörprüfung durchgeführt wurde.\n'
    ),
    'kit_sources': (
        '# Quellen: Titel\n'
        '\n'
        'Die Quellen der Rechercheergebnisse, auf die sich diese Folge stützt, in der Reihenfolge ihrer '
        'ersten Verwendung.\n'
        '\n'
        '1. Ashish Vaswani, Noam Shazeer (2017): *Attention is All you Need*. <https://arxiv.org/abs/1706.03762>\n'
        '2. *Ein Buch \\[Band 2\\]*?\n'
    ),
    'kit_no_sources': (
        '# Quellen: Titel\n'
        '\n'
        'Diese Folge stützt sich auf keine Quelle der Recherche.\n'
    ),
    'kit_description': (
        'Eine kurze Beschreibung der Folge.\n'
        '\n'
        'Ein zweiter Absatz.\n'
        '\n'
        'Kapitel\n'
        '00:00 Anfang\n'
        '02:00 Ende\n'
        '\n'
        'Quellen\n'
        'Ashish Vaswani, Noam Shazeer (2017): Attention is All you Need. https://arxiv.org/abs/1706.03762\n'
        'Ein Buch [Band 2]?'
    ),
    'teaching_plan': (
        '# Lehrplan: ep_001\n'
        '\n'
        '## Ausgangspunkt\n'
        '\n'
        'No specialist knowledge.\n'
        '\n'
        'How does a model compare candidates?\n'
        '\n'
        'Choose a compatible outcome.\n'
        '\n'
        'Explain which candidate is preferred and why.\n'
        '\n'
        '## Spannungsbogen\n'
        '\n'
        'Dramaturgie: Entdeckungsgeschichte\n'
        '\n'
        'Einstieg: Anekdote\n'
        '\n'
        'Rolle der fragenden Stimme: Skeptisch\n'
        '\n'
        'Große Idee: A score decides between candidates.\n'
        '\n'
        'Leitfrage: Why would lower be better?\n'
        '\n'
        'Naheliegende erste Antwort: A higher score sounds better.\n'
        '\n'
        'Wendepunkt: The energy convention turns it around.\n'
        '\n'
        'Auflösung: Lower energy means a better fit.\n'
        '\n'
        'Rückgriff auf den Anfang: The two candidates from the opening.\n'
        '\n'
        '## Lernziele\n'
        '\n'
        '### goal_compare: Compare a new pair of candidates.\n'
        '\n'
        'Which candidate is preferred and why?\n'
        '\n'
        '- Each candidate receives a score.\n'
        '- The lower score indicates the better fit.\n'
        '\n'
        '## Gedankengang\n'
        '\n'
        '### scene_example: What is scored?\n'
        '\n'
        '- Compare two possibilities.\n'
        '- Explain the limit.\n'
        '\n'
        'Compare the alternatives.\n'
        '\n'
        'Kapitelende: Abschluss\n'
        '\n'
        '## Durchgearbeitetes Beispiel\n'
        '\n'
        'Two candidate outcomes have different scores.\n'
        '\n'
        '- Compare their scores.\n'
        '- Select the lower one because it indicates fit.\n'
        '\n'
        'Mögliche Fehlvorstellung: Lower is always worse.\n'
        '\n'
        'This score uses the lower-is-better convention.\n'
        '\n'
        'Grenze: Scores alone do not provide probabilities.\n'
        '\n'
        '## Synthese und Übertragung\n'
        '\n'
        '- Assess each possible outcome.\n'
        '- Use the comparison to choose an outcome.\n'
        '\n'
        'Assessment can guide a decision.\n'
        '\n'
        'Compare two new outcomes.\n'
    ),
    'research_needed': (
        '# Recherche für die Erklärung ergänzen\n'
        '\n'
        '- Wie wird gelernt?\n'
        '\n'
        '  Der Mechanismus fehlt.\n'
        '\n'
        '- Woher kommen die Werte?\n'
        '\n'
        '  Belege fehlen.\n'
    ),
    'plan_with_limits': (
        '# Lehrplan: ep_001\n'
        '\n'
        '## Ausgangspunkt\n'
        '\n'
        'No specialist knowledge.\n'
        '\n'
        'How does a model compare candidates?\n'
        '\n'
        'Choose a compatible outcome.\n'
        '\n'
        'Explain which candidate is preferred and why.\n'
        '\n'
        '## Lernziele\n'
        '\n'
        '### goal_compare: Compare a new pair of candidates.\n'
        '\n'
        'Which candidate is preferred and why?\n'
        '\n'
        '- Each candidate receives a score.\n'
        '- The lower score indicates the better fit.\n'
        '\n'
        '## Gedankengang\n'
        '\n'
        '### scene_example: What is scored?\n'
        '\n'
        '- Compare two possibilities.\n'
        '- Explain the limit.\n'
        '\n'
        'Compare the alternatives.\n'
        '\n'
        '## Durchgearbeitetes Beispiel\n'
        '\n'
        'Two candidate outcomes have different scores.\n'
        '\n'
        '- Compare their scores.\n'
        '- Select the lower one because it indicates fit.\n'
        '\n'
        'Mögliche Fehlvorstellung: Lower is always worse.\n'
        '\n'
        'This score uses the lower-is-better convention.\n'
        '\n'
        'Grenze: Scores alone do not provide probabilities.\n'
        '\n'
        '## Synthese und Übertragung\n'
        '\n'
        '- Assess each possible outcome.\n'
        '- Use the comparison to choose an outcome.\n'
        '\n'
        'Assessment can guide a decision.\n'
        '\n'
        'Compare two new outcomes.\n'
        '\n'
        '## Eingeordnete Forschungsgrenzen\n'
        '\n'
        '- Für das Lernziel nicht nötig.\n'
    ),
    'research_resolved': (
        '# Recherchefragen geklärt\n'
        '\n'
        'Die Lehrplanung wurde mit den verfügbaren Quellen erneut geprüft und angenommen.\n'
        '\n'
        '- Wie wird gelernt?\n'
        '- Woher kommen die Werte?\n'
    ),
    'dossier': (
        '# Recherchedossier: Testthema\n'
        '\n'
        'Recherchelauf: `run_x`\n'
        '\n'
        'Ein begrenztes Dossier.\n'
        '\n'
        '2 Quellen eingelesen; 1 Abrufe/Importe fehlgeschlagen. Dem Modell wurden 3 ausgewählte '
        'Textabschnitte vorgelegt. Die Recherche wird gegen die vereinbarten Leitfragen geprüft; '
        'wissenschaftliche Unsicherheiten bleiben ausdrücklich erkennbar.\n'
        '\n'
        '## Befunde mit Quellenbezug\n'
        '\n'
        '### f_one — definition\n'
        '\n'
        'Konfigurationen erhalten Energien.\n'
        '\n'
        'Aussagetyp: source_definition / definition. Geltungsbereich: Beispiel; Test.\n'
        '\n'
        '- Einschränkung: Nur im Beispiel.\n'
        '**Bild zum Mitdenken:** Eine Waage.\n'
        '\n'
        '**Grenze des Bildes:** Keine echte Waage.\n'
        '\n'
        '- [Ein Fach Artikel](https://example.org/a), Seite 12 (`src_a#sec_0001`): „Models assign an energy“\n'
        '- [Notizen ohne Adresse](../sources/raw/run_x/src_b.html) (`src_b#sec_0001`): „Text“\n'
        '\n'
        '### f_two — claim\n'
        '\n'
        'Lernen und Schließen sind getrennt.\n'
        '\n'
        '- [Notizen ohne Adresse](../sources/raw/run_x/src_b.html) (`src_b#sec_0001`): „Text“\n'
        '\n'
        'Automatisierte inhaltliche Belegprüfung dokumentiert. Dies ist keine unabhängige empirische '
        'Bestätigung; deren Status steht je Befund im evidence_report.json.\n'
        '\n'
        '## Quellenübergreifende Vergleiche\n'
        '\n'
        '- Lernen (conditional_difference, resolved): Beide gelten. Bedingungen: Im Test. Befunde: f_one, f_two.\n'
        '\n'
        '## Abdeckung und Lücken\n'
        '\n'
        '- **Was ist Energie?** — answered; Befunde: f_one. \n'
        '\n'
        '- **Wie wird gelernt?** — partial; Befunde: f_two. Teilweise.\n'
        '\n'
        '- **Was bleibt offen?** — unanswered; Befunde: keine. Offen.\n'
        '\n'
        '## Offene Fragen\n'
        '\n'
        '- Wie genau?\n'
        '\n'
        '## Zugriffsprobleme\n'
        '\n'
        '- https://example.org/x: HTTP 403\n'
        '\n'
        '## Quellenverzeichnis\n'
        '\n'
        '- **Ein [Fach] Artikel** — A. Autor, B. Autorin, 2020. https://example.org/a — `src_a`. Metadaten '
        'und Extraktionsgrenzen: siehe `models/source_index.yaml`.\n'
        '- **Notizen ohne Adresse** — Autor nicht verifiziert, Datum unbekannt. sources/raw/run_x/src_b.html '
        '— `src_b`. Metadaten und Extraktionsgrenzen: siehe `models/source_index.yaml`.\n'
    ),
    'series_outline': (
        '# Serienentwurf\n'
        '\n'
        'Start with a concrete comparison.\n'
        '\n'
        'A bounded test plan.\n'
        '\n'
        '## ep_001: A model compares possibilities\n'
        '\n'
        'How are possibilities compared?\n'
        '\n'
        'Geplant: etwa 0.12 Minuten. Skript in diesem Lauf geprüft.\n'
    ),
    'script_show_notes': (
        '# Quellen und Hinweise: A model compares possibilities\n'
        '\n'
        'How are possibilities compared?\n'
        '\n'
        '## Kapitel\n'
        '\n'
        '- A concrete comparison\n'
        '\n'
        '## Grenzen und offene Vertiefungen\n'
        '\n'
        '- Training remains open.\n'
        '\n'
        '## Quellen\n'
        '\n'
        '- [Fixture paper](https://example.org/paper0)\n'
        '\n'
        '## Nachvollziehbarkeit\n'
        '\n'
        'Recherchelauf: `<research_run>`. Skriptlauf: `<script_run>`.\n'
        'Wissensreferenzen stehen im kanonischen Skript und führen über das Wissensmodell zu den Quellenabschnitten.\n'
    ),
    'published_teaching_plan': (
        '# Lehrplan: ep_001\n'
        '\n'
        '## Ausgangspunkt\n'
        '\n'
        'No specialist knowledge.\n'
        '\n'
        'How does a model compare candidates?\n'
        '\n'
        'Choose a compatible outcome.\n'
        '\n'
        'Explain which candidate is preferred and why.\n'
        '\n'
        '## Lernziele\n'
        '\n'
        '### goal_compare: Compare a new pair of candidates.\n'
        '\n'
        'Which candidate is preferred and why?\n'
        '\n'
        '- Each candidate receives a score.\n'
        '- The lower score indicates the better fit.\n'
        '\n'
        '## Gedankengang\n'
        '\n'
        '### scene_example: What is scored?\n'
        '\n'
        '- Compare two possibilities.\n'
        '- Explain the limit.\n'
        '\n'
        'Compare the alternatives.\n'
        '\n'
        '## Durchgearbeitetes Beispiel\n'
        '\n'
        'Two candidate outcomes have different scores.\n'
        '\n'
        '- Compare their scores.\n'
        '- Select the lower one because it indicates fit.\n'
        '\n'
        'Mögliche Fehlvorstellung: Lower is always worse.\n'
        '\n'
        'This score uses the lower-is-better convention.\n'
        '\n'
        'Grenze: Scores alone do not provide probabilities.\n'
        '\n'
        '## Synthese und Übertragung\n'
        '\n'
        '- Assess each possible outcome.\n'
        '- Use the comparison to choose an outcome.\n'
        '\n'
        'Assessment can guide a decision.\n'
        '\n'
        'Compare two new outcomes.\n'
    ),
}

# German goldens of the research and probe renderers, captured on 2026-10-07 before they moved to content_text.
GOLDEN_RESEARCH = {
    'quality_residual': (
        '# Recherchequalität\n'
        '\n'
        '1 von 2 Leitfragen erfüllen alle Qualitätsmerkmale.\n'
        '\n'
        '- Leitfrage vollständig beantwortet\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt\n'
        '\n'
        'Die Quellenprüfung hat fehlende Belege gefunden. Zuerst wird gezielt nachrecherchiert; die '
        'Bewertung aller Leitfragen wird danach erneuert. Die bisherigen Einzelbewertungen sind noch kein '
        'Urteil über den ergänzten Entwurf.\n'
        '\n'
        'Die Recherche wurde auf Wunsch der Redaktion mit dokumentierten Resteinwänden abgeschlossen '
        '(Notiz). Die letzte Gesamtprüfung hatte noch Einwände; sie stehen unten und gelten als offene '
        'Grenzen des Dossiers.\n'
        '\n'
        'Die Recherche wurde mit ausdrücklich akzeptierten Lücken abgeschlossen. Die betroffenen Teilfragen '
        'und verbliebenen Einwände stehen unten; das Dossier behauptet für sie keine Antwort.\n'
        '\n'
        'Die Recherche wurde mit vermerkten Grenzen abgeschlossen: 1 von 2 Leitfragen erfüllen alle '
        'Merkmale. Was offen oder nur eingeschränkt belegt ist, steht unten als Grenze (Quellengrenzen, '
        'unverändert vermerkte Einwände, Hinweise fürs Skript); dafür wird nicht weiter recherchiert.\n'
        '\n'
        '## Erfüllt: Frage A\n'
        '\n'
        'Grund A\n'
        '\n'
        '- Leitfrage vollständig beantwortet: erfüllt\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: erfüllt\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: erfüllt\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: erfüllt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: erfüllt\n'
        '- Als Grenze vermerkt: Vermerk\n'
        '- Grenze der verfügbaren Quellen: wird nicht weiter recherchiert und ist im Skript zu benennen\n'
        '- Als Grenze vermerkt: Keine Teilfrage dieser Leitfrage hat sich seit dem letzten Urteil geändert, '
        'und ihr Einwand wurde vermerkt, war strittig oder betrifft eine akzeptierte Lücke. Das Urteil '
        'bleibt, bis sich eine ihrer Antworten ändert; das Skript benennt die Grenze\n'
        '- Akzeptierte Lücke: Teilfrage task_a\n'
        '\n'
        '## Offen: Frage B\n'
        '\n'
        'Grund B\n'
        '\n'
        '- Leitfrage vollständig beantwortet: offen\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: offen\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: offen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: offen\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: offen\n'
        '- Noch benötigt: Beleg fehlt\n'
        '\n'
        '## Weitere offene Punkte\n'
        '\n'
        '- Lücke X\n'
        '\n'
        '## Hinweise fürs Skript\n'
        '\n'
        'Grenzen der verfügbaren Quellen und Punkte zu unveränderten Antworten, die die Folgebewertung '
        'nannte. Sie öffnen keine Recherche; das Skript benennt sie, wo es die betroffenen Aussagen verwendet.\n'
        '\n'
        '- Hinweis eins\n'
        '\n'
        '## Befunde aus nur einer Forschungsgruppe\n'
        '\n'
        'Beschreibend, nicht blockierend: Für diese Befunde stammen alle Belege aus einer bekannten Gruppe, '
        'oder die Gruppe ist unbekannt. Eine unabhängige Prüfung existiert für manche Aussagen von 2026 noch '
        'nicht; dann ist die Grenze zu benennen, nicht eine Quelle zu erzwingen.\n'
        '\n'
        '- f_a: unbekannte Gruppe (src_a)\n'
        '- f_b: Gruppe B (src_b, src_c)\n'
        '\n'
        '## Korpusprobe der Lücken\n'
        '\n'
        'Jede gemeldete Lücke wurde ohne Modellaufruf gegen die gespeicherten Abschnitte geprüft. Ein '
        'Treffer widerlegt die Lücke nicht; er benennt einen Abschnitt, der gelesen werden muss.\n'
        '\n'
        '- Lücke X — Treffer noch ungelesen: src_a#sec_0001\n'
        '- Lücke Y — kein passender Abschnitt gefunden\n'
        '- Lücke Z — something_new\n'
        '\n'
        '## Einschränkungen der Prüfung\n'
        '\n'
        'Die unabhängige Antwortprüfung hat diese Teilfragen bestanden, einzelne Aussagen aber nur mit '
        'Einschränkung bestätigt. Sie gelten als Grenzen der Befunde, nicht als offene Recherche.\n'
        '\n'
        '### Frage A\n'
        '\n'
        '- Nur eingeschränkt.\n'
        '\n'
        '## Akzeptierte Lücken\n'
        '\n'
        '- Teilfrage A: Kein Zugang\n'
        '- task_b\n'
        '- Abdeckungslücke\n'
        '\n'
        '## Strittige Prüfeinwände\n'
        '\n'
        'Die Gesamtprüfung hat diesen früheren Einwänden widersprochen; die Redaktion hat entschieden.\n'
        '\n'
        '- Einwand: Einwand eins\n'
        '  Prüfer: Prüfer eins\n'
        '  Entscheidung: dem Prüfer gefolgt, Einwand geschlossen (Notiz)\n'
        '- Einwand: obj_2\n'
        '  Prüfer: Prüfer zwei\n'
        '  Entscheidung: Einwand aufrechterhalten\n'
        '\n'
        '## Als Grenzen vermerkte Vollständigkeitseinwände\n'
        '\n'
        'Die geprüfte Antwort behandelt das jeweilige Kriterium mit belegten Befunden; die Gesamtprüfung '
        'hielt es für nicht ganz vollständig. Das steht hier als Grenze und wurde nicht erneut recherchiert.\n'
        '\n'
        '- Grenze eins\n'
        '\n'
        '## Einwände nach zwei Nachbesserungen\n'
        '\n'
        'Diese Teilfragen wurden zweimal nachgebessert und behalten ihre zuletzt geprüfte Antwort. Spätere '
        'Einwände der Gesamtprüfung stehen hier als Grenzen; sie haben den Lauf nicht mehr angehalten.\n'
        '\n'
        '- task_c: Einwand C\n'
        '\n'
        '## Einwände, die eine Nachbesserung nicht schließen konnte\n'
        '\n'
        'Die Nachbesserung dieser Teilfragen fand keine neuen Belege und endete blockiert. Sie behalten ihre '
        'zuvor geprüfte Antwort; der Einwand steht hier als Grenze.\n'
        '\n'
        '- task_d: Einwand D (Nachbesserung: keine Belege)\n'
        '- task_e: Einwand E\n'
        '\n'
        '## Nachprüfungen nach geänderten Voraussetzungen\n'
        '\n'
        'Beschreibend, nicht blockierend: So oft wurde eine geprüfte Antwort erneut gegen eine '
        'nachgebesserte Voraussetzung geprüft. Diese Nachprüfungen zählen nicht als Nachbesserung.\n'
        '\n'
        '- Teilfrage F: 2×\n'
        '- task_g: 1×\n'
        '\n'
        '## Verbliebene Prüfeinwände\n'
        '\n'
        '- Resteinwand\n'
    ),
    'quality_accepted': (
        '# Recherchequalität\n'
        '\n'
        '1 von 2 Leitfragen erfüllen alle Qualitätsmerkmale.\n'
        '\n'
        '- Leitfrage vollständig beantwortet\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt\n'
        '\n'
        'Die Quellenprüfung hat fehlende Belege gefunden. Zuerst wird gezielt nachrecherchiert; die '
        'Bewertung aller Leitfragen wird danach erneuert. Die bisherigen Einzelbewertungen sind noch kein '
        'Urteil über den ergänzten Entwurf.\n'
        '\n'
        'Die Recherche wurde mit ausdrücklich akzeptierten Lücken abgeschlossen. Die betroffenen Teilfragen '
        'und verbliebenen Einwände stehen unten; das Dossier behauptet für sie keine Antwort.\n'
        '\n'
        '## Erfüllt: Frage A\n'
        '\n'
        'Grund A\n'
        '\n'
        '- Leitfrage vollständig beantwortet: erfüllt\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: erfüllt\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: erfüllt\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: erfüllt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: erfüllt\n'
        '- Als Grenze vermerkt: Vermerk\n'
        '- Grenze der verfügbaren Quellen: wird nicht weiter recherchiert und ist im Skript zu benennen\n'
        '- Als Grenze vermerkt: Keine Teilfrage dieser Leitfrage hat sich seit dem letzten Urteil geändert, '
        'und ihr Einwand wurde vermerkt, war strittig oder betrifft eine akzeptierte Lücke. Das Urteil '
        'bleibt, bis sich eine ihrer Antworten ändert; das Skript benennt die Grenze\n'
        '- Akzeptierte Lücke: Teilfrage task_a\n'
        '\n'
        '## Offen: Frage B\n'
        '\n'
        'Grund B\n'
        '\n'
        '- Leitfrage vollständig beantwortet: offen\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: offen\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: offen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: offen\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: offen\n'
        '- Noch benötigt: Beleg fehlt\n'
        '\n'
        '## Weitere offene Punkte\n'
        '\n'
        '- Lücke X\n'
        '\n'
        '## Hinweise fürs Skript\n'
        '\n'
        'Grenzen der verfügbaren Quellen und Punkte zu unveränderten Antworten, die die Folgebewertung '
        'nannte. Sie öffnen keine Recherche; das Skript benennt sie, wo es die betroffenen Aussagen verwendet.\n'
        '\n'
        '- Hinweis eins\n'
        '\n'
        '## Befunde aus nur einer Forschungsgruppe\n'
        '\n'
        'Beschreibend, nicht blockierend: Für diese Befunde stammen alle Belege aus einer bekannten Gruppe, '
        'oder die Gruppe ist unbekannt. Eine unabhängige Prüfung existiert für manche Aussagen von 2026 noch '
        'nicht; dann ist die Grenze zu benennen, nicht eine Quelle zu erzwingen.\n'
        '\n'
        '- f_a: unbekannte Gruppe (src_a)\n'
        '- f_b: Gruppe B (src_b, src_c)\n'
        '\n'
        '## Korpusprobe der Lücken\n'
        '\n'
        'Jede gemeldete Lücke wurde ohne Modellaufruf gegen die gespeicherten Abschnitte geprüft. Ein '
        'Treffer widerlegt die Lücke nicht; er benennt einen Abschnitt, der gelesen werden muss.\n'
        '\n'
        '- Lücke X — Treffer noch ungelesen: src_a#sec_0001\n'
        '- Lücke Y — kein passender Abschnitt gefunden\n'
        '- Lücke Z — something_new\n'
        '\n'
        '## Einschränkungen der Prüfung\n'
        '\n'
        'Die unabhängige Antwortprüfung hat diese Teilfragen bestanden, einzelne Aussagen aber nur mit '
        'Einschränkung bestätigt. Sie gelten als Grenzen der Befunde, nicht als offene Recherche.\n'
        '\n'
        '### Frage A\n'
        '\n'
        '- Nur eingeschränkt.\n'
        '\n'
        '## Akzeptierte Lücken\n'
        '\n'
        '- Teilfrage A: Kein Zugang\n'
        '- task_b\n'
        '- Abdeckungslücke\n'
        '\n'
        '## Strittige Prüfeinwände\n'
        '\n'
        'Die Gesamtprüfung hat diesen früheren Einwänden widersprochen; die Redaktion hat entschieden.\n'
        '\n'
        '- Einwand: Einwand eins\n'
        '  Prüfer: Prüfer eins\n'
        '  Entscheidung: dem Prüfer gefolgt, Einwand geschlossen (Notiz)\n'
        '- Einwand: obj_2\n'
        '  Prüfer: Prüfer zwei\n'
        '  Entscheidung: Einwand aufrechterhalten\n'
        '\n'
        '## Als Grenzen vermerkte Vollständigkeitseinwände\n'
        '\n'
        'Die geprüfte Antwort behandelt das jeweilige Kriterium mit belegten Befunden; die Gesamtprüfung '
        'hielt es für nicht ganz vollständig. Das steht hier als Grenze und wurde nicht erneut recherchiert.\n'
        '\n'
        '- Grenze eins\n'
        '\n'
        '## Einwände nach zwei Nachbesserungen\n'
        '\n'
        'Diese Teilfragen wurden zweimal nachgebessert und behalten ihre zuletzt geprüfte Antwort. Spätere '
        'Einwände der Gesamtprüfung stehen hier als Grenzen; sie haben den Lauf nicht mehr angehalten.\n'
        '\n'
        '- task_c: Einwand C\n'
        '\n'
        '## Einwände, die eine Nachbesserung nicht schließen konnte\n'
        '\n'
        'Die Nachbesserung dieser Teilfragen fand keine neuen Belege und endete blockiert. Sie behalten ihre '
        'zuvor geprüfte Antwort; der Einwand steht hier als Grenze.\n'
        '\n'
        '- task_d: Einwand D (Nachbesserung: keine Belege)\n'
        '- task_e: Einwand E\n'
        '\n'
        '## Nachprüfungen nach geänderten Voraussetzungen\n'
        '\n'
        'Beschreibend, nicht blockierend: So oft wurde eine geprüfte Antwort erneut gegen eine '
        'nachgebesserte Voraussetzung geprüft. Diese Nachprüfungen zählen nicht als Nachbesserung.\n'
        '\n'
        '- Teilfrage F: 2×\n'
        '- task_g: 1×\n'
        '\n'
        '## Verbliebene Prüfeinwände zu akzeptierten Lücken\n'
        '\n'
        '- Resteinwand\n'
    ),
    'quality_noted': (
        '# Recherchequalität\n'
        '\n'
        '1 von 2 Leitfragen erfüllen alle Qualitätsmerkmale.\n'
        '\n'
        '- Leitfrage vollständig beantwortet\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt\n'
        '\n'
        'Die Quellenprüfung hat fehlende Belege gefunden. Zuerst wird gezielt nachrecherchiert; die '
        'Bewertung aller Leitfragen wird danach erneuert. Die bisherigen Einzelbewertungen sind noch kein '
        'Urteil über den ergänzten Entwurf.\n'
        '\n'
        'Die Recherche wurde mit ausdrücklich akzeptierten Lücken abgeschlossen. Die betroffenen Teilfragen '
        'und verbliebenen Einwände stehen unten; das Dossier behauptet für sie keine Antwort.\n'
        '\n'
        'Die Recherche wurde mit vermerkten Grenzen abgeschlossen: 1 von 2 Leitfragen erfüllen alle '
        'Merkmale. Was offen oder nur eingeschränkt belegt ist, steht unten als Grenze (Quellengrenzen, '
        'unverändert vermerkte Einwände, Hinweise fürs Skript); dafür wird nicht weiter recherchiert.\n'
        '\n'
        '## Erfüllt: Frage A\n'
        '\n'
        'Grund A\n'
        '\n'
        '- Leitfrage vollständig beantwortet: erfüllt\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: erfüllt\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: erfüllt\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: erfüllt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: erfüllt\n'
        '- Als Grenze vermerkt: Vermerk\n'
        '- Grenze der verfügbaren Quellen: wird nicht weiter recherchiert und ist im Skript zu benennen\n'
        '- Als Grenze vermerkt: Keine Teilfrage dieser Leitfrage hat sich seit dem letzten Urteil geändert, '
        'und ihr Einwand wurde vermerkt, war strittig oder betrifft eine akzeptierte Lücke. Das Urteil '
        'bleibt, bis sich eine ihrer Antworten ändert; das Skript benennt die Grenze\n'
        '- Akzeptierte Lücke: Teilfrage task_a\n'
        '\n'
        '## Offen: Frage B\n'
        '\n'
        'Grund B\n'
        '\n'
        '- Leitfrage vollständig beantwortet: offen\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: offen\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: offen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: offen\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: offen\n'
        '- Noch benötigt: Beleg fehlt\n'
        '\n'
        '## Weitere offene Punkte\n'
        '\n'
        '- Lücke X\n'
        '\n'
        '## Hinweise fürs Skript\n'
        '\n'
        'Grenzen der verfügbaren Quellen und Punkte zu unveränderten Antworten, die die Folgebewertung '
        'nannte. Sie öffnen keine Recherche; das Skript benennt sie, wo es die betroffenen Aussagen verwendet.\n'
        '\n'
        '- Hinweis eins\n'
        '\n'
        '## Befunde aus nur einer Forschungsgruppe\n'
        '\n'
        'Beschreibend, nicht blockierend: Für diese Befunde stammen alle Belege aus einer bekannten Gruppe, '
        'oder die Gruppe ist unbekannt. Eine unabhängige Prüfung existiert für manche Aussagen von 2026 noch '
        'nicht; dann ist die Grenze zu benennen, nicht eine Quelle zu erzwingen.\n'
        '\n'
        '- f_a: unbekannte Gruppe (src_a)\n'
        '- f_b: Gruppe B (src_b, src_c)\n'
        '\n'
        '## Korpusprobe der Lücken\n'
        '\n'
        'Jede gemeldete Lücke wurde ohne Modellaufruf gegen die gespeicherten Abschnitte geprüft. Ein '
        'Treffer widerlegt die Lücke nicht; er benennt einen Abschnitt, der gelesen werden muss.\n'
        '\n'
        '- Lücke X — Treffer noch ungelesen: src_a#sec_0001\n'
        '- Lücke Y — kein passender Abschnitt gefunden\n'
        '- Lücke Z — something_new\n'
        '\n'
        '## Einschränkungen der Prüfung\n'
        '\n'
        'Die unabhängige Antwortprüfung hat diese Teilfragen bestanden, einzelne Aussagen aber nur mit '
        'Einschränkung bestätigt. Sie gelten als Grenzen der Befunde, nicht als offene Recherche.\n'
        '\n'
        '### Frage A\n'
        '\n'
        '- Nur eingeschränkt.\n'
        '\n'
        '## Akzeptierte Lücken\n'
        '\n'
        '- Teilfrage A: Kein Zugang\n'
        '- task_b\n'
        '- Abdeckungslücke\n'
        '\n'
        '## Strittige Prüfeinwände\n'
        '\n'
        'Die Gesamtprüfung hat diesen früheren Einwänden widersprochen; die Redaktion hat entschieden.\n'
        '\n'
        '- Einwand: Einwand eins\n'
        '  Prüfer: Prüfer eins\n'
        '  Entscheidung: dem Prüfer gefolgt, Einwand geschlossen (Notiz)\n'
        '- Einwand: obj_2\n'
        '  Prüfer: Prüfer zwei\n'
        '  Entscheidung: Einwand aufrechterhalten\n'
        '\n'
        '## Als Grenzen vermerkte Vollständigkeitseinwände\n'
        '\n'
        'Die geprüfte Antwort behandelt das jeweilige Kriterium mit belegten Befunden; die Gesamtprüfung '
        'hielt es für nicht ganz vollständig. Das steht hier als Grenze und wurde nicht erneut recherchiert.\n'
        '\n'
        '- Grenze eins\n'
        '\n'
        '## Einwände nach zwei Nachbesserungen\n'
        '\n'
        'Diese Teilfragen wurden zweimal nachgebessert und behalten ihre zuletzt geprüfte Antwort. Spätere '
        'Einwände der Gesamtprüfung stehen hier als Grenzen; sie haben den Lauf nicht mehr angehalten.\n'
        '\n'
        '- task_c: Einwand C\n'
        '\n'
        '## Einwände, die eine Nachbesserung nicht schließen konnte\n'
        '\n'
        'Die Nachbesserung dieser Teilfragen fand keine neuen Belege und endete blockiert. Sie behalten ihre '
        'zuvor geprüfte Antwort; der Einwand steht hier als Grenze.\n'
        '\n'
        '- task_d: Einwand D (Nachbesserung: keine Belege)\n'
        '- task_e: Einwand E\n'
        '\n'
        '## Nachprüfungen nach geänderten Voraussetzungen\n'
        '\n'
        'Beschreibend, nicht blockierend: So oft wurde eine geprüfte Antwort erneut gegen eine '
        'nachgebesserte Voraussetzung geprüft. Diese Nachprüfungen zählen nicht als Nachbesserung.\n'
        '\n'
        '- Teilfrage F: 2×\n'
        '- task_g: 1×\n'
        '\n'
        '## Verbliebene Prüfeinwände, als Grenzen vermerkt oder strittig\n'
        '\n'
        '- Resteinwand\n'
    ),
    'probe_suffixes': (
        'Korpusprobe: kein passender Abschnitt in den gespeicherten Quellen.\n'
        'Korpusprobe: in den gespeicherten Quellen beantwortet.\n'
        'Korpusprobe: gelesen und bestätigt trotz Treffern in src_a#sec_0001, src_b#sec_0002.\n'
        'Korpusprobe: Treffer in src_a#sec_0001, src_b#sec_0002, die keine Folge nutzt.\n'
        'Korpusprobe: ungelesene Treffer in src_a#sec_0001, src_b#sec_0002.\n'
    ),
    'ledger_awaiting': (
        '# Recherchefragen\n'
        '\n'
        '1 von 3 Teilfragen geprüft abgeschlossen.\n'
        '\n'
        '1 Teilfragen als Lücke ausdrücklich akzeptiert.\n'
        '\n'
        'Der Rechercheplan wartet auf Freigabe; bis dahin wird kein weiterer Modellaufruf verbraucht.\n'
        '\n'
        'Mindestens 7 weitere Modellaufrufe, davon 4 für Dossier und Abschlussprüfung; 40 verfügbar. '
        'Erfahrungsgemäß etwa 18 Aufrufe (9 je offener Teilfrage, Erfahrungswert des Projekts). Zusätzliche '
        'Lese-, Such- und Korrekturschritte können mehr benötigen.\n'
        '\n'
        '## Was ist Energie?\n'
        '\n'
        'Status: verified\n'
        '\n'
        'Eine Zahl.\n'
        '\n'
        '- Abschlusskriterium: Definition\n'
        '- Abschlusskriterium: Beispiel\n'
        '\n'
        'Energie ordnet.\n'
        '- [Ein Fach Artikel](https://example.org/a), Seite 12\n'
        '- [Notizen](https://example.org/b)\n'
        '\n'
        '## Wie wird gelernt?\n'
        '\n'
        'Status: blocked (akzeptierte Lücke)\n'
        '\n'
        'Suche läuft\n'
        '\n'
        '\n'
        'Kein Zugang zur Quelle.\n'
    ),
    'ledger_running': (
        '# Recherchefragen\n'
        '\n'
        '1 von 3 Teilfragen geprüft abgeschlossen.\n'
        '\n'
        'Mindestens 7 weitere Modellaufrufe, davon 4 für Dossier und Abschlussprüfung; 40 verfügbar. '
        'Erfahrungsgemäß etwa 18 Aufrufe (9 je offener Teilfrage, Standardwert). Zusätzliche Lese-, Such- '
        'und Korrekturschritte können mehr benötigen.\n'
        '\n'
        '## Was ist Energie?\n'
        '\n'
        'Status: verified\n'
        '\n'
        'Eine Zahl.\n'
        '\n'
        '- Abschlusskriterium: Definition\n'
        '- Abschlusskriterium: Beispiel\n'
        '\n'
        'Energie ordnet.\n'
        '- [Ein Fach Artikel](https://example.org/a), Seite 12\n'
        '- [Notizen](https://example.org/b)\n'
        '\n'
        '## Wie wird gelernt?\n'
        '\n'
        'Status: blocked (akzeptierte Lücke)\n'
        '\n'
        'Suche läuft\n'
        '\n'
        '\n'
        'Kein Zugang zur Quelle.\n'
    ),
    'open_questions': (
        '# Offene Recherchefragen\n'
        '\n'
        '- Wie genau? Korpusprobe: kein passender Abschnitt in den gespeicherten Quellen.\n'
        '\n'
        '- Teilweise.\n'
        '- Offen. Korpusprobe: ungelesene Treffer in src_a#sec_0001.\n'
        '\n'
        '- Akzeptierte Lücke: Teilfrage A (Kein Zugang)\n'
        '- Akzeptierte Lücke: task_b\n'
    ),
    'open_questions_plain': (
        '# Offene Recherchefragen\n'
        '\n'
        '- Wie genau? Korpusprobe: kein passender Abschnitt in den gespeicherten Quellen.\n'
        '\n'
        '- Teilweise.\n'
        '- Offen. Korpusprobe: ungelesene Treffer in src_a#sec_0001.\n'
    ),
    'probe_research_needed': (
        '# Gemeldete Lücken mit ungelesenen Korpustreffern\n'
        '\n'
        '- Lücke A\n'
        '\n'
        '  Die Korpusprobe hat zu dieser gemeldeten Lücke passende, noch ungelesene Abschnitte gefunden: '
        'src_a#sec_0001. Diese Abschnitte müssen gelesen werden, bevor die Lücke behauptet wird.\n'
        '\n'
        '- Lücke B\n'
        '\n'
        '  Die Korpusprobe hat zu dieser gemeldeten Lücke passende, noch ungelesene Abschnitte gefunden: '
        'src_b#sec_0002. Diese Abschnitte müssen gelesen werden, bevor die Lücke behauptet wird.\n'
    ),
    'fixture_questions': (
        '# Recherchefragen\n'
        '\n'
        '1 von 1 Teilfragen geprüft abgeschlossen.\n'
        '\n'
        'Mindestens 0 weitere Modellaufrufe, davon 0 für Dossier und Abschlussprüfung; 742 verfügbar. '
        'Erfahrungsgemäß etwa 0 Aufrufe (16 je offener Teilfrage, Standardwert). Zusätzliche Lese-, Such- '
        'und Korrekturschritte können mehr benötigen.\n'
        '\n'
        '## What is energy?\n'
        '\n'
        'Status: verified\n'
        '\n'
        'Configurations receive energies in this fixture.\n'
        '\n'
        '- Abschlusskriterium: Explain energy in this bounded example.\n'
        '\n'
        'Configurations are assigned energies.\n'
        '- [Fixture paper](https://example.org/paper0)\n'
    ),
    'fixture_quality': (
        '# Recherchequalität\n'
        '\n'
        '1 von 1 Leitfragen erfüllen alle Qualitätsmerkmale.\n'
        '\n'
        '- Leitfrage vollständig beantwortet\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt\n'
        '\n'
        '## Erfüllt: Test topic\n'
        '\n'
        'The synthetic fixture meets this bounded requirement.\n'
        '\n'
        '- Leitfrage vollständig beantwortet: erfüllt\n'
        '- Grundlagen, Mechanismus und nachvollziehbares Beispiel: erfüllt\n'
        '- Inhaltliche Belege aus gelesenen unabhängigen Quellen: erfüllt\n'
        '- Gegenpositionen und unabhängige Prüfungen berücksichtigt: erfüllt\n'
        '- Geltungsgrenzen, Unsicherheit und Verbindungen erklärt: erfüllt\n'
    ),
    'fixture_open_questions': (
        '# Offene Recherchefragen\n'
        '\n'
        '\n'
        '\n'
        '\n'
    ),
}



# The transparency note D-154 adds to the end of the show notes (both), the listening sheet and the companion kit's
# description: the note of PRODUCT.md's roadmap and one line on what was made with AI. Everything before it is the
# German golden above, unchanged.
NOTE_DE = ("Dieser Output ist eine quellengebundene Synthese. Er ersetzt keine fachliche, rechtliche, medizinische "
           "oder wissenschaftliche Begutachtung. Unsichere oder widersprüchliche Quellenlagen werden markiert.")
AI_DE = ("KI-Hinweis: Das Skript dieser Folge wurde von einem Sprachmodell geschrieben und wird von synthetischen "
         "Stimmen gesprochen.")
NOTE_EN = ("This output is a source-bound synthesis. It does not replace a subject-matter, legal, medical or "
           "scientific assessment. Uncertain or contradictory source situations are marked.")
AI_EN = "AI notice: the script of this episode was written by a language model and is spoken by synthetic voices."
MARKED_DE = f"\n## Transparenzhinweis\n\n{NOTE_DE}\n\n{AI_DE}\n"
MARKED_EN = f"\n## Transparency note\n\n{NOTE_EN}\n\n{AI_EN}\n"


class GermanResearchGoldenTests(unittest.TestCase):
    """The research and probe files a reader opens, byte for byte as a German project wrote them before (D-153)."""

    def test_quality(self):
        from podcast_automate.research_quality import render_quality
        for key, flags in QUALITY_VARIANTS.items():
            with self.subTest(key=key):
                self.assertEqual(render_quality(quality_report(**flags)), GOLDEN_RESEARCH[key])

    def test_probe_suffixes(self):
        from podcast_automate.research_gap_probe import suffix
        self.assertEqual("\n".join(suffix(row) for row in probe_rows()) + "\n", GOLDEN_RESEARCH["probe_suffixes"])

    def test_question_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            self.assertEqual(render_ledger(folder), GOLDEN_RESEARCH["ledger_awaiting"])
            self.assertEqual(render_ledger(folder, phase="questions", accepted=0, source="unknown"),
                             GOLDEN_RESEARCH["ledger_running"])

    def test_research_run_files(self):
        fixture = fixtures.script_project(self)
        root = fixture.root.resolve()
        for name in ("questions", "quality", "open_questions"):
            with self.subTest(name=name):
                self.assertEqual((root / "research" / f"{name}.md").read_text(encoding="utf-8"),
                                 GOLDEN_RESEARCH[f"fixture_{name}"])

    def test_open_questions(self):
        """Captured from the expression research.py wrote inline before render_open_questions."""
        from podcast_automate.research import render_open_questions
        dossier, _, _, _ = dossier_inputs()
        accepted = [{"task_id": "task_a", "question": "Teilfrage A", "reason": "Kein Zugang"}, {"task_id": "task_b"}]
        self.assertEqual(render_open_questions(dossier, accepted, OPEN_PROBES, language="de-DE"),
                         GOLDEN_RESEARCH["open_questions"])
        self.assertEqual(render_open_questions(dossier, [], OPEN_PROBES, language="de-DE"),
                         GOLDEN_RESEARCH["open_questions_plain"])

    def test_probe_research_needed(self):
        """Captured from the expression script_pipeline.py wrote inline before render_probe_research_needed."""
        from podcast_automate.script_pipeline import render_probe_research_needed
        self.assertEqual(render_probe_research_needed(PROBE_QUESTIONS, language="de-DE"),
                         GOLDEN_RESEARCH["probe_research_needed"])


class GermanGoldenTests(unittest.TestCase):
    """Byte for byte what a German project wrote before content_text.py (D-153); the files D-154 marks end with the
    transparency note as their only change."""

    def test_download_names(self):
        from podcast_automate.downloads import PodcastDownload, Recording, episode_filename
        title = "Übersetzung: Wie rechnet Aufmerksamkeit?"
        name = lambda *args, **options: episode_filename("Transformer", "ep_003", *args, language="de-DE", **options)
        self.assertEqual(name(title, 1, 1, 1), GOLDEN["file_single"])
        self.assertEqual(name(title, 1, 2, 3), GOLDEN["file_part"])
        self.assertEqual(name(title, 1, 2, 3, in_archive=True), GOLDEN["archive_part"])
        self.assertEqual(name("Lang " * 40, 1, 1, 1, in_archive=True), GOLDEN["archive_long"])
        rows = [Recording("a", Path("a"), "a", "ep_001", "a"), Recording("b", Path("b"), "b", "ep_002", "b")]
        self.assertEqual(PodcastDownload("Transformer", rows, 2, "de-DE").filename, GOLDEN["zip_all"])
        self.assertEqual(PodcastDownload("Transformer", rows[:1], 3, "de-DE").filename, GOLDEN["zip_some"])

    def test_export_notes_and_listening_sheet(self):
        from podcast_automate.episode_audio import render_export_notes, render_listening_sheet
        script = episode_script()
        # D-154: both end with the transparency note now; the text before it is unchanged.
        self.assertEqual(render_export_notes(script, PARTED, VOICES, OVERRIDES, HOSTS, language="de-DE"),
                         GOLDEN["notes_parted"] + MARKED_DE)
        self.assertEqual(render_export_notes(script, [], VOICES, {}, language="de-DE"),
                         GOLDEN["notes_untimed"] + MARKED_DE)
        self.assertEqual(render_listening_sheet(script, ONE_PART, language="de-DE"), GOLDEN["sheet_one_part"] + MARKED_DE)
        self.assertEqual(render_listening_sheet(script, PARTED, language="de-DE"), GOLDEN["sheet_parted"] + MARKED_DE)
        self.assertEqual(render_listening_sheet(script, [], language="de-DE"), GOLDEN["sheet_untimed"] + MARKED_DE)

    def test_companion_kit_texts(self):
        from podcast_automate.publish_kit import compose_description, sources_markdown
        self.assertEqual(sources_markdown("Titel", kit_rows(), "de-DE"), GOLDEN["kit_sources"])
        self.assertEqual(sources_markdown("Titel", [], "de-DE"), GOLDEN["kit_no_sources"])
        # D-154: the description ends with the transparency note as plain text.
        self.assertEqual(compose_description(KIT_LONG, kit_chapters(), kit_rows(), "de-DE"),
                         (GOLDEN["kit_description"] + f"\n\n{NOTE_DE}\n{AI_DE}", 2))

    def test_teaching_plan(self):
        from podcast_automate.teaching import render_teaching_plan
        self.assertEqual(render_teaching_plan(told_design(), language="de-DE"), GOLDEN["teaching_plan"])

    def test_research_needed_and_plan_with_limits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            needed, plan, resolved = run_teaching_gaps(Path(temporary).resolve() / "teaching")
        self.assertEqual(needed, GOLDEN["research_needed"])
        self.assertEqual(plan, GOLDEN["plan_with_limits"])
        self.assertEqual(resolved, GOLDEN["research_resolved"])

    def test_research_dossier(self):
        from podcast_automate.research import render_dossier
        dossier, discovery, index, context = dossier_inputs()
        self.assertEqual(render_dossier(dossier, discovery, index, context, "run_x", language="de-DE"), GOLDEN["dossier"])

    def test_published_script_files(self):
        fixture = fixtures.script_project(self)
        texts = published_files(fixture, fixture.root.resolve())
        self.assertEqual(texts["research/series_outline.md"], GOLDEN["series_outline"])
        # D-154: the script run's show notes end with the transparency note.
        self.assertEqual(texts["episodes/ep_001/show_notes.md"], GOLDEN["script_show_notes"] + MARKED_DE)
        self.assertEqual(texts["episodes/ep_001/teaching_plan.md"], GOLDEN["published_teaching_plan"])


class ContentTableTests(unittest.TestCase):
    def test_every_language_has_every_entry_with_the_same_fields(self):
        """A renderer formats an entry with the same values in every language; a missing key or field would fail only
        for that language's projects."""
        import string
        from podcast_automate.content_text import CATALOG_LABELS, TEXT
        from podcast_automate.dramaturgy import DRAMATURGIES, ENDINGS, OPENINGS, STANCES
        from podcast_automate.question_budget import SOURCE_LABELS
        from podcast_automate.research_quality import CRITERIA, PROBE_LABELS
        fields = lambda entry: {name for _, name, _, _ in string.Formatter().parse(entry) if name}
        self.assertEqual(set(TEXT), {"de-DE", "en-US"})
        self.assertEqual(set(TEXT["de-DE"]), set(TEXT["en-US"]))
        for key, german in TEXT["de-DE"].items():
            with self.subTest(key=key):
                self.assertEqual(fields(german), fields(TEXT["en-US"][key]))
                self.assertTrue(TEXT["en-US"][key].strip())
        catalogs = {"dramaturgy": DRAMATURGIES, "opening": OPENINGS, "partner_stance": STANCES, "ending": ENDINGS,
                    "criterion": CRITERIA, "probe_status": PROBE_LABELS, "call_source": SOURCE_LABELS}
        self.assertEqual({name: set(table) for name, table in catalogs.items()},
                         {name: set(table) for name, table in CATALOG_LABELS["en-US"].items()})

    def test_a_language_without_a_table_reads_german_as_before(self):
        from podcast_automate.content_text import text
        self.assertEqual(text("fr-FR", "episode", number=3), "Folge 03")


class EnglishResearchFileTests(unittest.TestCase):
    """The research and probe files of an English podcast in English (D-153); what code or a prompt reads is not."""

    def test_quality(self):
        from podcast_automate.research_quality import render_quality
        rendered = render_quality(quality_report(), language="en-US")
        for line in ("# Research quality", "1 of 2 guiding questions meet every quality criterion.",
                     "- Guiding question fully answered", "## Met: Frage A",
                     "- Content evidence from read, independent sources: met", "## Open: Frage B",
                     "- Limits of validity, uncertainty and connections explained: open", "- Still needed: Beleg fehlt",
                     "- Noted as a limit: Vermerk", "- Accepted gap: sub-question task_a", "## Further open points",
                     "## Notes for the script", "- f_a: unknown group (src_a)", "## Corpus probe of the gaps",
                     "- Lücke X — hits not read yet: src_a#sec_0001", "- Lücke Y — no matching section found",
                     "- Lücke Z — something_new", "## Limitations of the review", "## Accepted gaps",
                     "- Teilfrage A: Kein Zugang", "## Disputed review objections", "- Objection: Einwand eins",
                     "  Reviewer: Prüfer eins", "  Decision: followed the reviewer, objection closed (Notiz)",
                     "  Decision: objection upheld", "## Completeness objections noted as limits",
                     "## Objections after two reworks", "## Objections a rework could not close",
                     "- task_d: Einwand D (rework: keine Belege)", "## Re-checks after changed prerequisites",
                     "- Teilfrage F: 2×", "## Remaining review objections"):
            self.assertIn("\n" + line + "\n", "\n" + rendered + "\n")
        self.assertIn("with documented residual objections (Notiz).", rendered)
        # The same lines in the same places as the German file: only the fixed words differ.
        self.assertEqual(len(rendered.splitlines()), len(GOLDEN_RESEARCH["quality_residual"].splitlines()))
        for key, heading in (("quality_accepted", "## Remaining review objections on accepted gaps"),
                             ("quality_noted", "## Remaining review objections, noted as limits or disputed")):
            self.assertIn(heading + "\n", render_quality(quality_report(**QUALITY_VARIANTS[key]), language="en-US"))

    def test_probe_suffixes_open_questions_and_research_needed(self):
        from podcast_automate.research import render_open_questions
        from podcast_automate.research_gap_probe import suffix
        from podcast_automate.script_pipeline import render_probe_research_needed
        self.assertEqual([suffix(row, language="en-US") for row in probe_rows()], [
            "Corpus probe: no matching section in the stored sources.",
            "Corpus probe: answered in the stored sources.",
            "Corpus probe: read and confirmed despite hits in src_a#sec_0001, src_b#sec_0002.",
            "Corpus probe: hits in src_a#sec_0001, src_b#sec_0002, which no episode uses.",
            "Corpus probe: unread hits in src_a#sec_0001, src_b#sec_0002."])
        dossier, _, _, _ = dossier_inputs()
        accepted = [{"task_id": "task_a", "question": "Teilfrage A", "reason": "Kein Zugang"}, {"task_id": "task_b"}]
        self.assertEqual(render_open_questions(dossier, accepted, OPEN_PROBES, language="en-US"),
                         "# Open research questions\n\n"
                         "- Wie genau? Corpus probe: no matching section in the stored sources.\n\n- Teilweise.\n"
                         "- Offen. Corpus probe: unread hits in src_a#sec_0001.\n\n"
                         "- Accepted gap: Teilfrage A (Kein Zugang)\n- Accepted gap: task_b\n")
        # The reader's file in English; the why_needed the supplementary research reads stays as it was.
        self.assertEqual(render_probe_research_needed(PROBE_QUESTIONS, language="en-US"),
                         "# Reported gaps with unread corpus hits\n\n- Lücke A\n\n  The corpus probe found matching "
                         "sections for this reported gap that are still unread: src_a#sec_0001. These sections must be "
                         "read before the gap is claimed.\n\n- Lücke B\n\n  The corpus probe found matching sections for "
                         "this reported gap that are still unread: src_b#sec_0002. These sections must be read before "
                         "the gap is claimed.\n")

    def test_question_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            rendered = render_ledger(Path(temporary).resolve(), language="en-US")
        self.assertEqual(rendered, "# Research questions\n\n1 of 3 sub-questions checked and closed.\n\n"
                                   "1 sub-questions explicitly accepted as gaps.\n\n"
                                   "The research plan awaits approval; until then no further model call is spent.\n\n"
                                   "At least 7 more model calls, 4 of them for the dossier and the final check; 40 "
                                   "available. From experience about 18 calls (9 per open sub-question, the project's "
                                   "experience). Additional reading, search and correction steps can need more.\n\n"
                                   "## Was ist Energie?\n\nStatus: verified\n\nEine Zahl.\n\n"
                                   "- Completion criterion: Definition\n- Completion criterion: Beispiel\n\n"
                                   "Energie ordnet.\n- [Ein Fach Artikel](https://example.org/a), page 12\n"
                                   "- [Notizen](https://example.org/b)\n\n## Wie wird gelernt?\n\n"
                                   "Status: blocked (accepted gap)\n\nSuche läuft\n\n\nKein Zugang zur Quelle.\n")

    def test_an_english_research_run_writes_english_reader_files(self):
        """The wiring: the research run hands the project's language to every reader file it writes."""
        from podcast_automate.research import run_research
        from podcast_automate.storage import write_yaml
        from tests.research_fixtures import ResearchProjectCase
        case = ResearchProjectCase()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.config = case.config.model_copy(update={"language": "en-US"})
        write_yaml(case.root / "project.yaml", case.config.model_dump(mode="json"))
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=case.model):
            self.assertEqual(run_research(case.root).status, "completed")
        research = case.root / "research"
        firsts = {name: (research / name).read_text(encoding="utf-8").splitlines()[0]
                  for name in ("questions.md", "quality.md", "open_questions.md", "research_briefing.md")}
        self.assertEqual(firsts, {"questions.md": "# Research questions", "quality.md": "# Research quality",
                                  "open_questions.md": "# Open research questions",
                                  "research_briefing.md": "# Research dossier: Test topic"})
        quality = (research / "quality.md").read_text(encoding="utf-8")
        self.assertIn("\n- Guiding question fully answered\n", quality)
        self.assertNotIn("Leitfrage", quality)


class EnglishExportTests(unittest.TestCase):
    """An English podcast's files and download names in English (D-153), with the English transparency note (D-154)."""

    def test_download_names(self):
        from podcast_automate.downloads import PodcastDownload, Recording, episode_filename
        title = "Translation: How does attention compute?"
        name = lambda *args, **options: episode_filename("Transformer", "ep_003", *args, language="en-US", **options)
        self.assertEqual(name(title, 1, 1, 1), "Transformer - Episode 03 - Translation How does attention compute.mp3")
        self.assertEqual(name(title, 1, 2, 3, in_archive=True),
                         "Episode 03 - Translation How does attention compute - Part 02 of 03.mp3")
        rows = [Recording("a", Path("a"), "a", "ep_001", "a"), Recording("b", Path("b"), "b", "ep_002", "b")]
        self.assertEqual(PodcastDownload("Transformer", rows, 2, "en-US").filename, "Transformer - All episodes.zip")
        self.assertEqual(PodcastDownload("Transformer", rows[:1], 3, "en-US").filename,
                         "Transformer - 1 of 3 episodes.zip")
        # The budgets were measured with the German words: "Episode" is two bytes longer than "Folge", so the title
        # gives them back. At its budget (a title without spaces is cut exactly there) a name inside the ZIP is as long
        # as the German one; a single download's title budget leaves the part out, as before, so its shorter "Part"
        # makes it a byte shorter. The ZIP's own name keeps its length too.
        for parts, in_archive in ((1, True), (3, True), (1, False), (3, False)):
            german, english = (episode_filename("T", "ep_003", "x" * 200, 1, 2, parts, language=language,
                                                in_archive=in_archive) for language in ("de-DE", "en-US"))
            compare = self.assertEqual if in_archive else self.assertLessEqual
            compare(len(english.encode("utf-8")), len(german.encode("utf-8")), (german, english))
        self.assertEqual(len(PodcastDownload("x" * 200, rows[:1], 12, "en-US").filename.encode("utf-8")),
                         len(PodcastDownload("x" * 200, rows[:1], 12, "de-DE").filename.encode("utf-8")))

    def test_export_notes_listening_sheet_and_readme(self):
        from podcast_automate.episode_audio import render_export_notes, render_export_readme, render_listening_sheet
        script = episode_script()
        self.assertEqual(render_export_notes(script, PARTED, VOICES, OVERRIDES, HOSTS, language="en-US"),
                         "# Wie ein Modell vergleicht\n\nAudio version with Mara and Jonas.\n"
                         "Voices: Erinome (Mara) and Sadachbia (Jonas).\n\n## Chapters\n\n- 0:00 Erstes Kapitel\n"
                         "- 0:00 Zweites Kapitel (part 2)\n\n## Pronunciation notes\n\n"
                         "These segments were recorded with a different spoken form; the text is unchanged.\n\n"
                         "- `seg_001`: Was vergleicht das Modell\n- `seg_002`: Zwei Moeglichkeiten.\n\n"
                         "Timestamps come from the measured assembly, not from an estimate.\n" + MARKED_EN)
        self.assertIn("- No timestamps are available for this version.",
                      render_export_notes(script, [], VOICES, {}, language="en-US"))
        self.assertEqual(render_listening_sheet(script, PARTED, language="en-US"),
                         "# Listening review: Wie ein Modell vergleicht\n\n"
                         "Fill in while listening. No check in the program can replace these columns.\n\n"
                         "| Part | Time | Chapter | Unclear | Attention lost | Pronunciation |\n"
                         "| --- | --- | --- | --- | --- | --- |\n| 1 | 0:00 | Erstes Kapitel | | | |\n"
                         "| 2 | 0:00 | Zweites Kapitel | | | |\n\n"
                         "After listening, tick in the Studio that the listening review was done.\n" + MARKED_EN)
        self.assertEqual(render_export_readme(script, [("audio.mp3", 600.0)], VOICES.voices, HOSTS,
                                              {"seg_001": "Anders"}, language="en-US"),
                         "# Wie ein Modell vergleicht\n\nFirst audio version for listening review.\n"
                         "Voices: Erinome (Mara) and Sadachbia (Jonas).\n\n"
                         "- [Listen to the episode](audio.mp3) – 10.00 minutes\n\n## Deviating spoken forms\n\n"
                         "- `seg_001`: Anders\n\nThe whole approved text is included in script order.\n"
                         "The technical assembly does not replace a listening review of pronunciation and naturalness.\n")

    def test_companion_kit_texts(self):
        from podcast_automate.publish_kit import DESCRIPTION_LIMIT, compose_description, sources_markdown
        self.assertEqual(sources_markdown("Title", kit_rows(), "en-US").splitlines()[:3],
                         ["# Sources: Title", "",
                          "The sources of the research findings this episode relies on, in the order of their first use."])
        text, listed = compose_description("A short description.", kit_chapters(), kit_rows(), "en-US")
        self.assertEqual((text, listed), ("A short description.\n\nChapters\n00:00 Anfang\n02:00 Ende\n\nSources\n"
                                          "Ashish Vaswani, Noam Shazeer (2017): Attention is All you Need. "
                                          "https://arxiv.org/abs/1706.03762\nEin Buch [Band 2]?\n\n"
                                          f"{NOTE_EN}\n{AI_EN}", 2))
        many = [{"title": f"Source {n}", "authors": [], "year": "", "url": "https://example.org/" + "x" * 300}
                for n in range(20)]
        text, listed = compose_description("A short description.", kit_chapters(), many, "en-US")
        self.assertLessEqual(len(text), DESCRIPTION_LIMIT)
        self.assertIn(f"\nFurther sources: {20 - listed}\n\n{NOTE_EN}\n{AI_EN}", text)

    def test_podcast_kit_texts(self):
        """The whole podcast's kit (D-165): its source list, its transcript and the AI notice naming the podcast."""
        from podcast_automate.content_text import transparency_text
        from podcast_automate.publish_kit import chapter_marks, podcast_sources_markdown, podcast_transcript
        rows = [{**row, "episodes": ["ep_002"]} for row in kit_rows()]
        self.assertEqual(podcast_sources_markdown("Topic", rows, {"ep_002": 2}, "en-US").splitlines()[2:5], [
            "The sources of the research findings the episodes rely on, in the order of their first use; in brackets "
            "the episodes that use them.", "",
            "1. Ashish Vaswani, Noam Shazeer (2017): *Attention is All you Need*. <https://arxiv.org/abs/1706.03762> "
            "(in Episode 02)"])
        script = episode_script()
        ai = ("AI notice: the scripts of this podcast were written by a language model and are spoken by synthetic "
              "voices.")
        self.assertEqual(transparency_text("en-US", podcast=True), f"{NOTE_EN}\n{ai}")
        self.assertEqual(podcast_transcript("Topic", [{"script": script, "number": 2, "recording": None,
                                                        "chapters": chapter_marks(script, None)}],
                                            {"host_a": "Host A", "host_b": "Host B"}, "en-US"),
                         "# Topic\n\nThe approved text of every episode in script order. The chapter timestamps come "
                         "from the measured assembly of the recording.\n\n## Episode 02: Wie ein Modell vergleicht\n\n"
                         "Not recorded yet: the chapters have no timestamps.\n\n### Erstes Kapitel\n\n"
                         "**Host A:** Was vergleicht das Modell?\n\n### Zweites Kapitel\n\n**Host B:** Zwei Möglichkeiten."
                         f"\n\n## Transparency note\n\n{NOTE_EN}\n\n{ai}\n")

    def test_teaching_plan_and_research_questions(self):
        from podcast_automate.teaching import render_research_needed, render_research_resolved, render_teaching_plan
        rendered = render_teaching_plan(told_design(), language="en-US")
        for line in ("# Teaching plan: ep_001", "## Starting point", "## Narrative arc",
                     "Dramaturgy: Story of a discovery", "Opening: Anecdote", "Role of the questioning voice: Skeptical",
                     "Big idea: A score decides between candidates.", "Guiding question: Why would lower be better?",
                     "Obvious first answer: A higher score sounds better.",
                     "Turning point: The energy convention turns it around.",
                     "Payoff: Lower energy means a better fit.", "Callback to the opening: The two candidates from the "
                     "opening.", "## Learning objectives", "## Line of reasoning", "Chapter ending: Conclusion",
                     "## Worked example", "Possible misconception: Lower is always worse.",
                     "Limit: Scores alone do not provide probabilities.", "## Synthesis and transfer"):
            self.assertIn(f"\n{line}\n" if not line.startswith("# ") else f"{line}\n", rendered)
        german = GOLDEN["teaching_plan"]
        # The same lines in the same places: only the fixed words differ.
        self.assertEqual(len(rendered.splitlines()), len(german.splitlines()))
        gap = ResearchGap(question="How is it learned?", why_needed="The mechanism is missing.")
        self.assertEqual(render_research_needed([gap], language="en-US"),
                         "# Research to add for the explanation\n\n- How is it learned?\n\n  The mechanism is missing.\n")
        self.assertEqual(render_research_resolved(["How is it learned?"], language="en-US"),
                         "# Research questions resolved\n\nThe teaching plan was checked again with the available "
                         "sources and accepted.\n\n- How is it learned?\n")

    def test_research_dossier(self):
        from podcast_automate.research import render_dossier
        dossier, discovery, index, context = dossier_inputs()
        rendered = render_dossier(dossier, discovery, index, context, "run_x", language="en-US")
        for line in ("# Research dossier: Testthema", "Research run: `run_x`", "## Findings with source references",
                     "Claim type: source_definition / definition. Scope: Beispiel; Test.",
                     "- Qualification: Nur im Beispiel.", "**Illustration:** Eine Waage.",
                     "**Limit of the illustration:** Keine echte Waage.",
                     "- [Ein Fach Artikel](https://example.org/a), page 12 (`src_a#sec_0001`): „Models assign an energy“",
                     "## Comparisons across sources", "## Coverage and gaps",
                     "- **Was bleibt offen?** — unanswered; Findings: none. Offen.", "## Open questions",
                     "## Access problems", "## Sources",
                     "- **Notizen ohne Adresse** — Author not verified, Date unknown. sources/raw/run_x/src_b.html — "
                     "`src_b`. Metadata and extraction limits: see `models/source_index.yaml`."):
            self.assertIn(line + "\n", rendered)
        self.assertIn("2 sources read; 1 retrievals or imports failed. The model was given 3 selected text sections.",
                      rendered)
        self.assertEqual(len(rendered.splitlines()), len(GOLDEN["dossier"].splitlines()))

    def test_an_english_script_run_publishes_english_files(self):
        """The wiring: publish_scripts and the teaching stage pass the project's language to the renderers."""
        from podcast_automate.storage import write_yaml
        fixture = fixtures.script_project(self)
        root = fixture.root.resolve()
        write_yaml(root / "project.yaml", fixture.config.model_copy(update={"language": "en-US"}).model_dump(mode="json"))
        texts = published_files(fixture, root)
        self.assertEqual(texts["research/series_outline.md"].splitlines()[0], "# Series outline")
        self.assertIn("\nPlanned: about 0.12 minutes. Script checked in this run.\n", texts["research/series_outline.md"])
        notes = texts["episodes/ep_001/show_notes.md"]
        self.assertTrue(notes.startswith("# Sources and notes: A model compares possibilities\n"), notes)
        self.assertIn("\nResearch run: `<research_run>`. Script run: `<script_run>`.\n", notes)
        self.assertTrue(notes.endswith(MARKED_EN), notes)
        self.assertTrue(texts["episodes/ep_001/teaching_plan.md"].startswith(
            "# Teaching plan: ep_001\n\n## Starting point\n"), texts["episodes/ep_001/teaching_plan.md"])

    def test_script_run_files(self):
        from podcast_automate.research_models import SourceIndex
        from podcast_automate.script_artifacts import render_series_outline, render_show_notes
        plan = fixtures.example_plan()
        self.assertEqual(render_series_outline(plan, plan.episodes, language="en-US"),
                         "# Series outline\n\nStart with a concrete comparison.\n\nA bounded test plan.\n\n"
                         "## ep_001: A model compares possibilities\n\nHow are possibilities compared?\n\n"
                         "Planned: about 0.12 minutes. Script checked in this run.\n")
        self.assertIn("Planned only so far.", render_series_outline(plan, [], language="en-US"))
        dossier, _, index, _ = dossier_inputs()
        entry = plan.episodes[0].model_copy(update={"finding_ids": ["f_one"]})
        notes = render_show_notes(fixtures.example_script(), entry, dossier, {s.id: s for s in index.sources},
                                  research_id="run_r", run_id="run_s", language="en-US")
        self.assertEqual(notes, "# Sources and notes: A model compares possibilities\n\nHow are possibilities compared?\n\n"
                                "## Chapters\n\n- A concrete comparison\n\n## Limits and open deep dives\n\n"
                                "- Training remains open.\n\n## Sources\n\n- [Ein Fach Artikel](https://example.org/a)\n"
                                "- [Notizen ohne Adresse](../../sources/raw/run_x/src_b.html)\n\n## Traceability\n\n"
                                "Research run: `run_r`. Script run: `run_s`.\nKnowledge references are in the canonical "
                                "script and lead through the knowledge model to the source sections.\n" + MARKED_EN)
        self.assertIsInstance(index, SourceIndex)


if __name__ == "__main__":
    unittest.main()
