from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGING_FILE = ROOT / "infra" / "modal" / "sonora_staging.py"


class StagingIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = STAGING_FILE.read_text(encoding="utf-8")

    def test_uses_separate_modal_app(self) -> None:
        self.assertRegex(self.source, r'APP_NAME\s*=\s*["\']sonora-staging["\']')

    def test_uses_staging_only_resources(self) -> None:
        expected = {
            "sonora-staging-checkpoints",
            "sonora-staging-audio",
            "sonora-staging-jobs",
            "sonora-staging-portal",
        }
        self.assertTrue(expected.issubset(set(re.findall(r'["\'](sonora-staging-[a-z-]+)["\']', self.source))))
        forbidden = [
            "ace-step-instinct-portal",
            "ace-step-15-api",
            "ace-step-checkpoints",
            "ace-step-audio",
            "ace-step-jobs",
            "ace-step-portal",
        ]
        for resource in forbidden:
            self.assertNotIn(resource, self.source)

    def test_no_deployment_or_generation_runs_at_import_time(self) -> None:
        self.assertNotRegex(self.source, r"(?m)^\s*modal\s+deploy\b")
        self.assertNotIn(".remote(", self.source)
        self.assertNotIn(".spawn(", self.source.split("def submit_generation", 1)[0])


if __name__ == "__main__":
    unittest.main()
