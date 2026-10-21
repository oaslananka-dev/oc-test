"""Offline replay tests for the capacity-10 evidence acceptance boundary."""
import json
import tempfile
import unittest
from pathlib import Path

import ocfilo_f2_capacity_audit as audit
import ocfilo_f2_dag as dag
import ocfilo_f2_pilot as pilot
from ocfilo_f2_sched import schedule

SHA = "a" * 40
RUN_ID = 98765432


def evidence(task, round_no=1, failed=False):
    return {
        "schema_version": 2, "repository": pilot.REPO,
        "repository_id": int(pilot.REPO_ID), "issue_number": 10,
        "model": pilot.MODEL, "identity": "actions-readonly",
        "allowed_paths": [task["allowed_paths"][0]], "task_id": task["task_id"],
        "checkout_sha": SHA, "round": round_no,
        "idempotency_key": pilot.idempotency_key(SHA, task["task_id"]),
        "workflow_run_id": RUN_ID, "run_attempt": 1,
        "worker_outcome": "FAILED" if failed else "SUCCEEDED",
        "failure_class": "TIMEOUT" if failed else None,
        "retryable": failed,
        "agent_exit_code": 124 if failed else 0,
        "agent_seconds": 600 if failed else 15,
        "deadline_seconds": 600,
        "scope_verified": not failed,
        "published": False,
    }


def patch(path):
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..abcd1234\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        "+three observations\n"
    ).encode()


class CapacityAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = dag._plan("capacity-10")
        self.tasks = dag.read_dag(self.raw)
        self._build()

    def _build(self, recovered=True):
        all_evidence = []
        for tid, task in self.tasks.items():
            attempts = [(1, True), (2, False)] if tid == "f2-cap10-e" and recovered else [(1, False)]
            for round_no, failed in attempts:
                ev = evidence(task, round_no, failed)
                directory = self.root / f"ocfilo-f2-{RUN_ID}-1-r{round_no}-{tid}"
                directory.mkdir()
                (directory / "result.json").write_text(json.dumps(ev) + "\n")
                if not failed:
                    (directory / "changes.patch").write_bytes(patch(task["allowed_paths"][0]))
                all_evidence.append(ev)
        state = schedule(self.raw, all_evidence, SHA, capacity=1)
        directory = self.root / f"ocfilo-f2-dagstate-{RUN_ID}-1"
        directory.mkdir()
        (directory / "dag-state.json").write_text(json.dumps(state) + "\n")

    def check(self):
        return audit.audit(self.root, RUN_ID, 1, SHA)

    def test_complete_with_bounded_recovery_is_not_first_try_clean(self):
        report = self.check()
        self.assertTrue(report["bundle_verified"])
        self.assertEqual(report["outcome"], "COMPLETE")
        self.assertEqual(report["artifact_attempts"], 11)
        self.assertEqual(report["patches_verified"], 10)
        self.assertEqual(report["recovered_tasks"], ["f2-cap10-e"])
        self.assertFalse(report["first_try_all_ten"])
        self.assertFalse(report["github_workflow_conclusion_checked"])
        self.assertFalse(report["publishing_authorized"])

    def test_clean_ten_task_fixture_is_first_try(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._build(recovered=False)
        report = self.check()
        self.assertEqual(report["artifact_attempts"], 10)
        self.assertTrue(report["first_try_all_ten"])
        self.assertEqual(report["recovered_tasks"], [])

    def test_wrong_native_run_sha_and_attempt_rejected(self):
        for args in ((RUN_ID + 1, 1, SHA), (RUN_ID, 2, SHA), (RUN_ID, 1, "b" * 40),
                     (RUN_ID, 1, "bad")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                audit.audit(self.root, *args)

    def test_duplicate_json_key_and_unknown_root_key_rejected(self):
        file = self.root / f"ocfilo-f2-{RUN_ID}-1-r1-f2-cap10-a/result.json"
        original = file.read_text()
        for extra in (',"task_id":"f2-cap10-b"', ',"unreviewed":true',
                      ',"extra":{"nested":1,"nested":2}'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                file.write_text(original.rstrip()[:-1] + extra + "}")
                self.check()
        file.write_text(original)

    def test_evidence_scope_and_publication_tamper_rejected(self):
        file = self.root / f"ocfilo-f2-{RUN_ID}-1-r1-f2-cap10-a/result.json"
        original = file.read_text()
        for key, value in (("allowed_paths", ["playground/f2/dag/other.txt"]),
                           ("published", True), ("checkout_sha", "b" * 40)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ev = json.loads(original)
                ev[key] = value
                file.write_text(json.dumps(ev))
                self.check()
        file.write_text(original)

    def test_patch_foreign_scope_and_missing_patch_rejected(self):
        directory = self.root / f"ocfilo-f2-{RUN_ID}-1-r1-f2-cap10-a"
        p = directory / "changes.patch"
        original = p.read_bytes()
        with self.assertRaises(ValueError):
            p.write_bytes(patch("playground/f2/dag/wrong.txt"))
            self.check()
        p.unlink()
        with self.assertRaises(ValueError):
            self.check()
        p.write_bytes(original)

    def test_extra_files_missing_evidence_symlink_rejected(self):
        directory = self.root / f"ocfilo-f2-{RUN_ID}-1-r1-f2-cap10-a"
        extra = directory / "debug.log"
        extra.write_text("not allowed")
        with self.assertRaises(ValueError):
            self.check()
        extra.unlink()
        path = directory / "result.json"
        original = path.read_bytes()
        path.unlink()
        path.symlink_to(self.root / f"ocfilo-f2-{RUN_ID}-1-r1-f2-cap10-b/result.json")
        with self.assertRaises(ValueError):
            self.check()
        path.unlink()
        path.write_bytes(original)
        extra = self.root / "unrelated-directory"
        extra.mkdir()
        with self.assertRaises(ValueError):
            self.check()

    def test_state_mismatch_duplicate_keys_and_unknown_file_rejected(self):
        state_dir = self.root / f"ocfilo-f2-dagstate-{RUN_ID}-1"
        state = state_dir / "dag-state.json"
        original = state.read_text()
        bad = json.loads(original)
        bad["outcome"] = "HALTED"
        state.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            self.check()
        state.write_text(original.rstrip()[:-1] + ',"outcome":"COMPLETE"}')
        with self.assertRaises(ValueError):
            self.check()
        state.write_text(original)
        extra = state_dir / "unused"
        extra.write_text("")
        with self.assertRaises(ValueError):
            self.check()


if __name__ == "__main__":
    unittest.main()
