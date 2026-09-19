import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate.qwen_worker import cache_key, cached_segment, hash_file, segment_settings, spoken_settings


class WorkerCacheTests(unittest.TestCase):
    def test_cache_is_reused_only_when_file_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "segment.wav"
            meta = Path(temporary) / "segment.json"
            wav.write_bytes(b"fixture")
            meta.write_text(json.dumps({"sha256": hash_file(wav), "render_seconds": 12}))
            self.assertEqual(cached_segment(wav, meta)["render_seconds"], 12)
            wav.write_bytes(b"changed")
            self.assertIsNone(cached_segment(wav, meta))
            meta.write_text("[]")
            self.assertIsNone(cached_segment(wav, meta))
            meta.write_text("unfinished")
            self.assertIsNone(cached_segment(wav, meta))


class SpokenTextKeyTests(unittest.TestCase):
    """The worker's own settings and key: a spoken form belongs to them only when it differs."""

    def settings(self, spoken=None):
        segment = {"segment_id": "seg_001", "speaker_id": "host_a", "text": "Auf H800 gerechnet."}
        if spoken is not None:
            segment["spoken_text"] = spoken
        return segment_settings(segment, voice="Aiden", language="German",
                                config={"tts_model": "m", "tts_device": "auto", "tts_attention": "eager", "seed": 42},
                                revision="r", device="cpu", dtype="torch.float32",
                                packages={"torch": "2.4"}, hip_version=None)

    def test_a_segment_without_a_spoken_form_keeps_the_key_it_had_before_spoken_forms(self):
        # The exact composition the worker hashed before spoken forms existed. Every cache entry
        # rendered by that worker keeps its key, so no episode is synthesised again.
        before = {"worker_version": 2, "model": "m", "revision": "r", "voice": "Aiden",
                  "text": "Auf H800 gerechnet.", "language": "German", "device": "auto",
                  "resolved_device": "cpu", "dtype": "torch.float32", "attention": "eager", "seed": 42,
                  "packages": {"torch": "2.4"}, "hip_version": None}
        expected = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
        self.assertEqual(self.settings(), before)
        self.assertEqual(cache_key(self.settings()), expected)
        # A request that spells out the unchanged text as its spoken form is the same request.
        self.assertEqual(cache_key(self.settings(spoken="Auf H800 gerechnet.")), expected)

    def test_a_spoken_form_enters_the_settings_and_changes_the_key(self):
        spoken = self.settings(spoken="Auf Ha achthundert gerechnet.")
        self.assertEqual((spoken["text"], spoken["spoken_text"]),
                         ("Auf H800 gerechnet.", "Auf Ha achthundert gerechnet."))
        self.assertNotEqual(cache_key(spoken), cache_key(self.settings()))
        self.assertNotEqual(cache_key(spoken), cache_key(self.settings(spoken="Auf Ha achthundert gerechnet")))

    def test_the_shared_rule_adds_spoken_text_only_when_it_differs(self):
        self.assertEqual(spoken_settings("Text.", None), {})
        self.assertEqual(spoken_settings("Text.", "Text."), {})
        self.assertEqual(spoken_settings("Text.", "Anders."), {"spoken_text": "Anders."})


if __name__ == "__main__":
    unittest.main()
