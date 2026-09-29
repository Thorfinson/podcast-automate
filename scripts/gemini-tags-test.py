"""Listen test: does Gemini 3.8 Flash TTS over OpenRouter act on inline audio tags such as <laugh> or <short pause>?

Google documents inline tags in angle brackets for Gemini 3.8 Flash TTS; OpenRouter's speech endpoint takes only
model, input, voice and format, so whether the tags arrive and are performed (not read aloud) needs a listen.
Each line below is spoken once plain and once with tags, with the voices of the current projects.

Run from the repository root (PowerShell):
    $env:PATH = "$PWD\\tools\\ffmpeg\\bin;$env:PATH"
    .\\.venv\\Scripts\\python.exe scripts\\gemini-tags-test.py

The OpenRouter key is read from OPENROUTER_API_KEY or asked for without echo; it is never written to disk.
About a dozen short requests, a few cents of OpenRouter credit.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from podcast_automate.speech import GEMINI_MODEL, GeminiSpeech  # noqa: E402

EXPERT, PARTNER = "Sadaltager", "Aoede"
# (name, voice, plain text, the same text with tags)
LINES = [
    ("01_pause", EXPERT,
     "Und genau hier wird es spannend. Die Zahl klingt beeindruckend, aber sie misst etwas anderes, als man denkt.",
     "Und genau hier wird es spannend. <short pause> Die Zahl klingt beeindruckend, aber sie misst etwas anderes, als man denkt."),
    ("02_laugh", PARTNER,
     "Moment, heißt das, die Abfrage läuft einfach durch, obwohl sie falsch ist? Das ist ja fast schon unheimlich.",
     "Moment, heißt das, die Abfrage läuft einfach durch, obwohl sie falsch ist? <laugh> Das ist ja fast schon unheimlich."),
    ("03_sigh", EXPERT,
     "Ja. Und das ist der Teil, den man in keiner Fehlermeldung sieht.",
     "<sigh> Ja. Und das ist der Teil, den man in keiner Fehlermeldung sieht."),
    ("04_breath", PARTNER,
     "Okay, lass mich das kurz zusammenfassen, bevor wir weitergehen.",
     "<breath> Okay, lass mich das kurz zusammenfassen, bevor wir weitergehen."),
    ("05_mixed", EXPERT,
     "Stell dir einen Sachbearbeiter vor, der jede Tabelle kennt, aber nicht weiß, was die Spalten bedeuten.",
     "Stell dir einen Sachbearbeiter vor, <short pause> der jede Tabelle kennt, <breath> aber nicht weiß, was die Spalten bedeuten. <laugh>"),
    # A written style direction, to hear whether it is spoken aloud as Google warns.
    ("06_style_in_text", PARTNER,
     "Das ist eine gute Nachricht für alle, die mit Daten arbeiten.",
     "Sag es fröhlich: Das ist eine gute Nachricht für alle, die mit Daten arbeiten."),
    # The further tags the expression layer may use (expression.ALLOWED_TAGS), one each.
    ("07_chuckle", PARTNER, "Das hätte ich jetzt nicht erwartet.", "<chuckle> Das hätte ich jetzt nicht erwartet."),
    ("08_giggle", PARTNER, "Okay, das ist fast schon lustig.", "Okay, <giggle> das ist fast schon lustig."),
    ("09_gasp", PARTNER, "Achtundsiebzig Prozent? Das ist enorm.", "<gasp> Achtundsiebzig Prozent? Das ist enorm."),
    ("10_exhales", EXPERT, "Gut. Fassen wir zusammen, was wir bisher wissen.", "<exhales> Gut. Fassen wir zusammen, was wir bisher wissen."),
    ("11_phew", EXPERT, "Das war ein langer Weg bis hierher.", "<phew> Das war ein langer Weg bis hierher."),
    ("12_tsk", EXPERT, "Genau das steht in keiner der Studien.", "<tsk> Genau das steht in keiner der Studien."),
    ("13_throat_clearing", EXPERT, "Eine Korrektur zu vorhin: Es waren fünfundvierzig Fragen.",
     "<throat-clearing> Eine Korrektur zu vorhin: Es waren fünfundvierzig Fragen."),
    ("14_long_pause", EXPERT, "Und damit zum zweiten Teil.", "<long pause> Und damit zum zweiten Teil."),
]


def diagnose(key):
    """One raw request per response format, printing status and OpenRouter's error text (never the key)."""
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    for fmt in ("pcm", "wav", "mp3"):
        payload = {"model": GEMINI_MODEL, "input": "Hallo, das ist ein kurzer Test.", "voice": EXPERT, "response_format": fmt}
        request = Request("https://openrouter.ai/api/v1/audio/speech", data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                body = response.read()
                print(f"  {fmt}: HTTP {response.status}, {response.headers.get('Content-Type')}, {len(body)} Bytes")
        except HTTPError as exc:
            text = exc.read().decode("utf-8", "replace").replace(key, "[Key]")
            print(f"  {fmt}: HTTP {exc.code}: {text[:600]}")


def main():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip() or getpass.getpass("OpenRouter-Key (wird nicht gespeichert): ").strip()
    if not key:
        sys.exit("Kein Key angegeben.")
    print("Diagnose des Speech-Aufrufs:")
    diagnose(key)
    print()
    engine = GeminiSpeech(key, timeout=300, model=GEMINI_MODEL)
    out = ROOT / ".studio" / "gemini-tags-test" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    ffmpeg = shutil.which("ffmpeg")
    print(f"Modell: {GEMINI_MODEL}\nAusgabe: {out}\n")
    # --neu: only the further tags (07 on); the first six were heard on 2026-09-29.
    lines = [line for line in LINES if line[0] >= "07"] if "--neu" in sys.argv else LINES
    for name, voice, plain, tagged in lines:
        for variant, text in (("ohne", plain), ("mit", tagged)):
            wav = engine.synthesize(text, voice, "de-DE", cache)
            target = out / f"{name}_{variant}.wav"
            shutil.copyfile(wav, target)
            if ffmpeg:
                subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(target), "-codec:a", "libmp3lame",
                                "-q:a", "3", str(target.with_suffix(".mp3"))], check=True)
                target.unlink()
            print(f"  {name}_{variant}  ({voice}): {text}")
    shutil.rmtree(cache, ignore_errors=True)
    print("\nFertig. Anhören und vergleichen: werden die Tags gespielt (Lachen, Seufzen, Pause) oder vorgelesen?")
    if os.name == "nt":
        os.startfile(out)  # noqa: S606 - opens the folder in Explorer


if __name__ == "__main__":
    main()
