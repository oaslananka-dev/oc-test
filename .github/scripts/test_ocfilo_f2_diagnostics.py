"""Offline F2 runner diagnostic privacy and workflow scope tests."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import ocfilo_f2_diagnostics as diag

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ocfilo-f2-dag-round.yml"


class DiagnosticsTests(unittest.TestCase):
    def test_only_fixed_numeric_signals_and_no_log_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.log"
            secret = "ghp_an_example_secret_that_must_never_be_printed"
            path.write_text(
                "session.processor step=3 token=" + secret + "\n"
                "::error::attempt to inject workflow command\n"
                "llm providerID=opencode modelID=big-pickle stream\n"
                "stream error socket connection was closed unexpectedly\n"
                "status 429 rate limit\n", encoding="utf-8")
            report = diag.summarize(path)
            self.assertEqual(report["status"], "sampled")
            self.assertEqual(report["signals"]["session_processor_lines"], 1)
            self.assertEqual(report["signals"]["stream_error_lines"], 1)
            self.assertEqual(report["signals"]["rate_limit_lines"], 1)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diag.main(["--log", str(path)])
            self.assertIsInstance(json.loads(output.getvalue().split("=", 1)[1]), dict)
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn("::error::", output.getvalue())
            self.assertNotIn("socket connection was closed", output.getvalue())

    def test_missing_symlink_and_large_log_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(diag.summarize(root / "missing")["status"], "unavailable")
            self.assertEqual(diag.summarize(root)["status"], "unavailable")
            target = root / "large.log"
            target.write_bytes(b"X" * diag.SAMPLE_LIMIT + b"\nstream error\n")
            report = diag.summarize(target)
            self.assertTrue(report["truncated"])
            self.assertLessEqual(report["sample_bytes"], diag.SAMPLE_LIMIT)
            self.assertEqual(report["signals"]["stream_error_lines"], 1)
            symlink = root / "alias"
            symlink.symlink_to(target)
            self.assertEqual(diag.summarize(symlink)["status"], "unavailable")

    def test_debug_only_for_reviewed_task_and_no_raw_log_tail(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"$F2_PLAN" = "capacity-10"', workflow)
        self.assertIn('"$F2_TASK_ID" = "f2-cap10-e"', workflow)
        self.assertIn('f2_debug=(--print-logs --log-level debug)', workflow)
        self.assertIn('opencode', workflow)
        self.assertIn("ocfilo_f2_diagnostics.py --log", workflow)
        self.assertNotIn('tail -n 30 "$f"', workflow)
        self.assertIn("contents: read", workflow)


if __name__ == "__main__":
    unittest.main()
