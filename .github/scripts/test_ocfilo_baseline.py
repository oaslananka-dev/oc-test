#!/usr/bin/env python3
"""Offline regression suite; no remote GitHub access or model keys."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from ocfilo_baseline import validate


def command(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class UniversalBaselineTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name) / "repo"
        self.root.mkdir()
        command(self.root, "init", "-q")
        command(self.root, "config", "user.name", "Test")
        command(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "README.md").write_text("# Test\n", encoding="utf-8")
        self.commit()
        self.base = command(self.root, "rev-parse", "HEAD")
    def commit(self):
        command(self.root, "add", "-A")
        command(self.root, "commit", "-qm", "fixture")
    def check(self):
        return validate(self.root, self.base)
    def test_clean_python(self):
        (self.root/"feature.py").write_text("def answer():\n    return 42\n")
        self.commit()
        self.assertEqual(self.check()["python_syntax_checked"], ["feature.py"])
    def test_syntax_error_rejected(self):
        (self.root/"broken.py").write_text("def broken(:\n    pass\n")
        self.commit()
        with self.assertRaises(ValueError): self.check()
    def test_no_execution_of_python_code(self):
        (self.root/"untrusted.py").write_text("raise RuntimeError('must not execute')\n")
        self.commit()
        self.assertEqual(self.check()["outcome"], "PASS")
    def test_shell_syntax_error_rejected(self):
        (self.root/"broken.sh").write_text("#!/bin/bash\nif then\n")
        self.commit()
        with self.assertRaises(ValueError): self.check()
    def test_clean_shell(self):
        (self.root/"good.sh").write_text("#!/bin/bash\nprintf '%s\\n' hello\n")
        self.commit()
        self.assertEqual(self.check()["shell_syntax_checked"], ["good.sh"])
    def test_git_diff_whitespace_error(self):
        (self.root/"README.md").write_text("# Test\nbad trailing space  \n")
        self.commit()
        with self.assertRaises(ValueError): self.check()
    def test_unrelated_base_refused(self):
        (self.root/"README.md").write_text("# Another\n")
        self.commit()
        with self.assertRaises(ValueError): validate(self.root, "b"*40)
    def test_no_change_diff(self):
        self.assertEqual(self.check()["changed_files"], 0)
    def test_filename_with_spaces(self):
        (self.root/"a file.py").write_text("x = 1\n")
        self.commit()
        self.assertEqual(self.check()["python_syntax_checked"], ["a file.py"])
    def test_deleted_files_do_not_fail_syntax(self):
        (self.root/"README.md").unlink()
        self.commit()
        self.assertEqual(self.check()["changed_files"], 0)
    def test_malicious_parent_path(self):
        (self.root/"valid.py").write_text("x = 1\n")
        self.commit()
        with self.assertRaises(ValueError): validate(self.root, self.base, "no-branch")


if __name__=="__main__":
    unittest.main()
