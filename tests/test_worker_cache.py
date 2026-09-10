import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate.qwen_worker import cached_segment, hash_file


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


if __name__ == "__main__":
    unittest.main()
