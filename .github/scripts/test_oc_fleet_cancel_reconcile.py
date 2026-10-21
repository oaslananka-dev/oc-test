#!/usr/bin/env python3
"""Offline-only synthetic 202/409/network/retry race and audit tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oc_fleet_cancel_admission import admit_offline
from oc_fleet_cancel_reconcile import (record_simulated_intent,
                                      record_simulated_http, reconcile_simulated)
from oc_fleet_supervisor import REPO, WORKFLOW, cancel_proposal

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
RUN = "39000009999"
SHA = "a"*40
LIVE = {
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


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name)/"cancel-audit.db"
        self.proposal = cancel_proposal(ITEM, NOW)
        self.pid = self.proposal["proposal_id"]
        fields = {"repository": REPO, "run_id": RUN, "attempt": "1",
                  "head_sha": SHA, "multica_issue": "OAS-12",
                  "task_id": "task-alpha"}
        approval = {"approved": True, "proposal_id": self.pid,
                    "run_id": RUN, "attempt": "1", "head_sha": SHA,
                    "task_id": "task-alpha", "actor": "test-owner"}
        result = admit_offline(
            self.proposal, github_reader=lambda _: LIVE,
            receipt_verifier=lambda _: fields,
            approval_verifier=lambda _f,_t: approval,
            audit_db=self.db, now=NOW)
        self.assertFalse(result["cancel_requested"])

    def tearDown(self):
        self.tmp.cleanup()

    def intent(self):
        return record_simulated_intent(self.db, self.pid)

    def http(self, code):
        return record_simulated_http(self.db, self.pid, code)

    def observed(self, **changes):
        return reconcile_simulated(self.db, self.pid, {**LIVE, **changes})

    def audit(self):
        with sqlite3.connect(self.db) as db:
            state = db.execute(
                "SELECT state,http_status FROM cancel_simulation WHERE proposal_id=?",
                (self.pid,)).fetchone()
            events = db.execute(
                "SELECT event,resulting_state FROM cancel_simulation_event "
                "WHERE proposal_id=? ORDER BY id", (self.pid,)).fetchall()
            return state, events

    def test_202_is_not_cancellation_without_terminal_reconciliation(self):
        self.assertEqual(self.intent()["state"], "outcome_unknown")
        accepted = self.http(202)
        self.assertEqual(accepted["state"], "accepted_unconfirmed")
        self.assertFalse(accepted["cancel_request_sent"])
        self.assertEqual(self.observed()["state"], "accepted_unconfirmed")
        observed = self.observed(status="completed", conclusion="cancelled")
        self.assertEqual(observed["state"], "terminal_cancelled_observed")
        self.assertEqual(observed["cancellation_attribution"], "not_proven")
        self.assertEqual(self.audit()[0], ("terminal_cancelled_observed", 202))

    def test_409_conflict_followed_by_success_does_not_claim_cancelled(self):
        self.intent()
        self.assertEqual(self.http(409)["state"], "conflict_unresolved")
        self.assertEqual(self.observed(status="completed", conclusion="success")["state"],
                         "terminal_other_observed")
        with self.assertRaises(ValueError):
            self.observed(status="completed", conclusion="cancelled")

    def test_network_ambiguous_or_5xx_cannot_autoretry(self):
        self.intent()
        unknown = self.http(None)
        self.assertEqual(unknown["state"], "outcome_unknown")
        self.assertTrue(unknown["no_automatic_retry"])
        self.assertEqual(self.intent()["result"], "repeat_suppressed")
        self.assertEqual(self.http(503)["state"], "outcome_unknown")
        self.assertEqual(self.http(503)["result"], "duplicate_response")
        with self.assertRaises(ValueError):
            self.http(202)
        self.assertEqual(self.observed()["result"], "still_nonterminal_no_retry")
        self.assertEqual(self.audit()[0][1], 503)

    def test_denied_is_not_cancelled_even_if_other_actor_cancelled(self):
        self.intent()
        self.assertEqual(self.http(403)["state"], "permission_denied")
        ending = self.observed(status="completed", conclusion="cancelled")
        self.assertEqual(ending["cancellation_attribution"], "not_proven")
        self.assertEqual(ending["state"], "terminal_cancelled_observed")
        with self.assertRaises(ValueError):
            self.http(202)

    def test_wrong_run_sha_attempt_or_workflow_never_writes_final_state(self):
        self.intent()
        self.http(202)
        for altered in (
            {"id": int(RUN)+1}, {"run_attempt": 2},
            {"head_sha": "b"*40},
            {"path": ".github/workflows/not-worker.yml"},
            {"repository": {"full_name": "evil/repo"}},
        ):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                self.observed(**altered)
        self.assertEqual(self.audit()[0], ("accepted_unconfirmed", 202))

    def test_terminal_transition_is_immutable_and_repeated_read_idempotent(self):
        self.intent()
        first = self.observed(status="completed", conclusion="failure")
        second = self.observed(status="completed", conclusion="failure")
        self.assertEqual(first["state"], "terminal_other_observed")
        self.assertEqual(second["result"], "terminal_already_recorded")
        self.assertEqual(len(self.audit()[1]), 2)
        with self.assertRaises(ValueError):
            self.observed()
        with self.assertRaises(ValueError):
            self.http(202)

    def test_eight_concurrent_intents_emit_exactly_one_no_send(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.intent(), range(8)))
        self.assertEqual(sum(r["result"]=="simulation_intent_recorded" for r in results), 1)
        self.assertEqual(sum(r["result"]=="repeat_suppressed" for r in results), 7)
        self.assertTrue(all(not r["cancel_request_sent"] for r in results))
        self.assertEqual(len(self.audit()[1]), 1)

    def test_unexpected_http_codes_and_types_are_rejected_without_audit(self):
        self.intent()
        for bad in (True, False, "202", 200, 204, 302, 404, 429):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.http(bad)
        self.assertEqual(self.audit()[0], ("outcome_unknown", None))
        self.assertEqual(len(self.audit()[1]), 1)

    def test_audit_events_have_utc_timestamp_without_sensitive_payload(self):
        self.intent()
        self.http(202)
        self.observed(status="completed", conclusion="cancelled")
        with sqlite3.connect(self.db) as db:
            events = db.execute(
                "SELECT event,recorded_at_utc FROM cancel_simulation_event "
                "ORDER BY id").fetchall()
            self.assertEqual(len(events), 3)
            for event, timestamp in events:
                self.assertTrue(timestamp.endswith("Z"))
                self.assertNotIn("secret", event.lower())
                self.assertNotIn("token", event.lower())

    def test_world_readable_symlink_or_unapproved_admission_blocked(self):
        original = self.db.stat().st_mode
        self.db.chmod(0o644)
        with self.assertRaisesRegex(ValueError,"permissions"):
            self.intent()
        self.db.chmod(original & 0o777)
        alias = Path(self.tmp.name)/"link.db"
        alias.symlink_to(self.db)
        with self.assertRaisesRegex(ValueError,"isolated"):
            record_simulated_intent(alias,self.pid)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE admission SET status='not_approved'")
        with self.assertRaisesRegex(ValueError,"missing offline admission"):
            self.intent()
        with sqlite3.connect(self.db) as db:
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='cancel_simulation'"
            ).fetchone())


if __name__ == "__main__":
    unittest.main()
