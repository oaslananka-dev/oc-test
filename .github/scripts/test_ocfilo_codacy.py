#!/usr/bin/env python3
"""Offline regression tests for ocfilo Codacy quality decisions."""
import json
import tempfile
import unittest
from pathlib import Path

from ocfilo_codacy import evaluate


class CodacyGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "report.json"
        self.data = {
            "issues": [],
            "errors": [],
            "toolResults": [
                {"toolId": "Ruff", "status": "success", "filesAnalyzed": 1, "errorCount": 0}
            ],
        }

    def check(self, status=0, paths=None):
        self.path.write_text(json.dumps(self.data), encoding="utf8")
        return evaluate(self.path, status, paths or ["playground/sample.py"])

    def test_clean(self):
        self.assertEqual(self.check()["blocking"], 0)

    def test_advisory(self):
        self.data["issues"] = [
            {"filePath": "playground/sample.py", "severity": "Info", "category": "CodeStyle"}
        ]
        self.assertEqual(self.check(1)["advisory"], 1)

    def test_high_finding_blocks(self):
        self.data["issues"] = [
            {"filePath": "playground/sample.py", "severity": "High", "category": "Security"}
        ]
        self.assertEqual(self.check(1)["blocking"], 1)

    def test_critical_finding_blocks(self):
        self.data["issues"] = [
            {"filePath": "playground/sample.py", "severity": "Critical", "category": "Security"}
        ]
        self.assertEqual(self.check(1)["blocking"], 1)

    def test_broken_scanner_rejected(self):
        with self.assertRaises(ValueError):
            self.check(2)

    def test_missing_ruff_rejected(self):
        self.data["toolResults"] = []
        with self.assertRaises(ValueError):
            self.check(0)

    def test_tool_errors_rejected(self):
        self.data["errors"] = [{"message": "scanner failed"}]
        with self.assertRaises(ValueError):
            self.check(0)

    def test_unapproved_path_rejected(self):
        self.data["issues"] = [
            {"filePath": ".github/workflows/ocfilo-worker.yml", "severity": "Critical"}
        ]
        with self.assertRaises(ValueError):
            self.check(1)

    def test_unknown_severity_rejected(self):
        self.data["issues"] = [{"filePath": "playground/sample.py", "severity": "unknown"}]
        with self.assertRaises(ValueError):
            self.check(1)

    def test_inconsistent_exit_code_rejected(self):
        self.data["issues"] = [{"filePath": "playground/sample.py", "severity": "Info"}]
        with self.assertRaises(ValueError):
            self.check(0)

    def test_partial_ruff_coverage_rejected(self):
        with self.assertRaises(ValueError):
            self.check(0, ["playground/a.py", "playground/b.py"])


if __name__ == "__main__":
    unittest.main()
