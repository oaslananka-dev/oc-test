#!/usr/bin/env python3
"""Fail-closed gate tests. No code path calls a GitHub mutating endpoint."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oc_fleet_cancel_admission import admit_offline
from oc_fleet_supervisor import REPO, WORKFLOW, cancel_proposal

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
RUN = "39000009999"
SHA = "a" * 40
STATE = {
    "id": int(RUN), "run_attempt": 1, "event": "workflow_dispatch",
    "repository": {"full_name": REPO}, "head_branch": "main",
    "path": WORKFLOW, "head_sha": SHA,
    "status": "in_progress", "conclusion": None,
}
ITEM = {
    "github": {"verified": True, "state": "nonterminal_at_github",
               "repository": REPO, "run_id": RUN, "attempt": "1",
               "head_sha": SHA, "workflow": WORKFLOW},
    "observation": {"run_id": RUN, "attempt": "1", "multica_issue": "OAS-12"},
}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.p = cancel_proposal(ITEM, NOW)
        self.receipt = {"repository": REPO, "run_id": RUN, "attempt": "1",
                        "head_sha": SHA, "multica_issue": "OAS-12", "task_id": "task-alpha"}
        self.approval = {"approved": True, "proposal_id": self.p["proposal_id"],
                         "run_id": RUN, "attempt": "1", "head_sha": SHA,
                         "task_id": "task-alpha", "actor": "owner-approved"}
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Path(self.tmp.name) / "cancel-audit.db"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, run):
        self.calls.append(("GET", run))
        return STATE

    def proof(self, fields):
        self.calls.append(("RECEIPT", fields["run_id"]))
        return self.receipt

    def permit(self, fields, task):
        self.calls.append(("APPROVAL", task))
        return self.approval

    def simulate(self, **kwargs):
        return admit_offline(kwargs.pop("proposal", self.p),
                             github_reader=kwargs.pop("github_reader", self.read),
                             receipt_verifier=kwargs.pop("receipt_verifier", self.proof),
                             approval_verifier=kwargs.pop("approval_verifier", self.permit),
                             audit_db=kwargs.pop("audit_db", self.audit),
                             now=kwargs.pop("now", NOW), **kwargs)

    def test_only_admission_is_written_and_duplicate_is_idempotent(self):
        first = self.simulate()
        second = self.simulate()
        self.assertEqual(first["result"], "recorded_offline_only")
        self.assertEqual(second["result"], "duplicate_recorded_offline")
        self.assertFalse(first["cancel_requested"])
        self.assertFalse(first["execution_enabled"])
        with sqlite3.connect(self.audit) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admission").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT status FROM admission").fetchone()[0],
                             "simulation_only_no_cancel")
        self.assertTrue(all(event[0] != "POST" for event in self.calls))
        self.assertEqual(self.audit.stat().st_mode & 0o777, 0o600)

    def test_concurrent_duplicates_create_only_one_audit_row(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.simulate(), range(8)))
        statuses = sorted(x["result"] for x in outcomes)
        self.assertEqual(statuses.count("recorded_offline_only"), 1)
        self.assertEqual(statuses.count("duplicate_recorded_offline"), 7)
        with sqlite3.connect(self.audit) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admission").fetchone()[0], 1)
        self.assertTrue(all(x["cancel_requested"] is False for x in outcomes))

    def test_same_proposal_different_actor_or_task_never_reuses_approval(self):
        self.simulate()
        other_actor = {**self.approval, "actor": "another-person"}
        with self.assertRaisesRegex(ValueError, "different evidence"):
            self.simulate(approval_verifier=lambda _f, _t: other_actor)
        other_task = {**self.receipt, "task_id": "different-task"}
        other_task_approval = {**self.approval, "task_id": "different-task"}
        with self.assertRaisesRegex(ValueError, "different evidence"):
            self.simulate(receipt_verifier=lambda _f: other_task,
                          approval_verifier=lambda _f, _t: other_task_approval)
        with sqlite3.connect(self.audit) as db:
            self.assertEqual(db.execute("SELECT task_id,actor FROM admission").fetchone(),
                             ("task-alpha", "owner-approved"))

    def test_expiry_or_forged_proposal_fails_before_any_callbacks(self):
        for change in (
            {"head_sha": "b" * 40},
            {"approval": "approved"},
            {"executable": True},
            {"expires_at_utc": (NOW + timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.simulate(proposal={**self.p, **change})
        with self.assertRaises(ValueError):
            self.simulate(now=NOW + timedelta(minutes=5))
        self.assertFalse(self.audit.exists())
        self.assertEqual(self.calls, [])

    def test_terminal_and_wrong_sha_are_rejected_before_receipt(self):
        for altered in (
            dict(STATE, status="completed", conclusion="success"),
            dict(STATE, head_sha="b"*40),
            dict(STATE, run_attempt=2),
            dict(STATE, repository={"full_name": "attacker/repo"}),
        ):
            self.calls.clear()
            with self.subTest(alt=altered), self.assertRaises(ValueError):
                self.simulate(github_reader=lambda _: altered)
            self.assertEqual(self.calls, [])
        self.assertFalse(self.audit.exists())

    def test_untrusted_manifest_and_wrong_approver_rejected(self):
        original = self.receipt
        for bad in (
            {**original, "head_sha": "b"*40},
            {**original, "task_id": "../escape"},
            {**original, "multica_issue": "OAS-999"},
            {**original, "task_id": ""},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.simulate(receipt_verifier=lambda _: bad)
        for bad in (
            {**self.approval, "approved": False},
            {**self.approval, "actor": ""},
            {**self.approval, "task_id": "other"},
            {**self.approval, "proposal_id": "b"*64},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.simulate(approval_verifier=lambda _f, _t: bad)
        self.assertFalse(self.audit.exists())

    def test_world_readable_or_linked_audit_destination_refused(self):
        insecure = Path(self.tmp.name) / "unsafe.db"
        insecure.write_text("not a database", encoding="ascii")
        insecure.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions unsafe"):
            self.simulate(audit_db=insecure)
        symlink = Path(self.tmp.name) / "pointer.db"
        symlink.symlink_to(insecure)
        with self.assertRaisesRegex(ValueError, "not isolated"):
            self.simulate(audit_db=symlink)
        self.assertFalse(self.audit.exists())

    def test_missing_verifiers_and_audit_isolation_fail_closed(self):
        with self.assertRaises(ValueError):
            self.simulate(approval_verifier=None)
        with self.assertRaises(ValueError):
            self.simulate(audit_db=Path(self.tmp.name)/"events.db")
        self.assertFalse(self.audit.exists())


if __name__ == "__main__":
    unittest.main()
