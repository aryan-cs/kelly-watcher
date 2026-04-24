from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class PerformanceResolutionTimingTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is required for dashboard helper tests")
    def test_node_helper_script(self) -> None:
        script = Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "performanceResolution.test.js"
        result = subprocess.run(
            ["node", str(script)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("performanceResolution helper tests passed", result.stdout)
