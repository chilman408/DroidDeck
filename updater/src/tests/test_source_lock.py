import json
import re
import unittest
from pathlib import Path


LOCK_PATH = Path(__file__).resolve().parents[1] / "config" / "source-lock.json"


class SourceLockTests(unittest.TestCase):
    def test_required_upstreams_have_immutable_revisions(self):
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

        self.assertIn("blissos", lock)
        self.assertIn("rpi4_baseline", lock)
        self.assertIn("frontend", lock)
        self.assertEqual("voyager-x86", lock["blissos"]["branch"])
        self.assertEqual("db973c9fec45d90f4b85de56c6711e0227716e69", lock["blissos"]["revision"])
        self.assertEqual("c3e5409dcd325b95d5d6b451d80efd15536fa1b9", lock["rpi4_baseline"]["revision"])
        for source in [lock["blissos"], lock["rpi4_baseline"], lock["frontend"], *lock["tesla_components"]]:
            self.assertTrue(source["url"].startswith("https://"))
            self.assertRegex(source["revision"], re.compile(r"^[0-9a-f]{40}$"))

    def test_manifest_components_include_capture_and_location_paths(self):
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        paths = {source["path"] for source in lock["tesla_components"]}

        self.assertTrue({
            "external/libws",
            "external/tesla-android-virtual-display",
            "external/tesla-android-virtual-touchscreen",
            "external/tesla-android-virtual-gnss",
            "external/tesla-android-configuration-manager",
            "external/tesla-android-audio-relay",
            "vendor/tesla-android",
        }.issubset(paths))
        for source in lock["tesla_components"]:
            self.assertIn(source["license_status"], ("GPL-3.0", "unresolved"))


if __name__ == "__main__":
    unittest.main()
