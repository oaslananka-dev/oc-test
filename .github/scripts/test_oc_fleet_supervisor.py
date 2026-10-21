#!/usr/bin/env python3
"""Offline tests: no GitHub writes and no bogus running/completed claims."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oc_fleet_supervisor import (REPO, WORKFLOW, cancel_proposal, get_github_run,
                                 main, snapshot, verified_state)
from oc_fleet_telemetry_bridge import Store

RUN = "38731018050"
SHA = "a" * 40
LIVE = {
    "id": int(RUN), "run_attempt": 1, "event": "workflow_dispatch",
    "repository": {"full_name": REPO}, "head_branch": "main",
    "path": WORKFLOW, "head_sha": SHA,
    "status": "in_progress", "conclusion": None,
}


class SupervisorTests(unittest.TestCase):
    def test_completed_github_overrides_ambiguous_step_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / "events.db"
            store = Store(dbpath)
            store.register(RUN, "1", "OAS-12")
            store.ingest(RUN, "1", [{"run_id": RUN, "attempt": "1", "seq": 1,
                                     "kind": "step_start"}])
            completed = dict(LIVE, status="completed", conclusion="success")
            with patch("oc_fleet_supervisor.get_github_run", return_value=completed):
                from oc_fleet_observe import read_only
                with read_only(dbpath) as db:
                    item = snapshot(db, RUN, "1", 2)
                self.assertEqual(item["observation"]["last_observed_phase"],
                                 "step_started")
                self.assertEqual(item["github"]["state"], "completed")
                self.assertEqual(item["github"]["github_conclusion"], "success")
                with self.assertRaisesRegex(ValueError, "no cancellable"):
                    cancel_proposal(item)
                output = StringIO()
                with redirect_stdout(output):
                    main(["--db", str(dbpath), "--run", RUN])
                d = json.loads(output.getvalue())
                self.assertEqual(d["verified_run"]["github"]["state"], "completed")
                self.assertNotIn("secret", output.getvalue())

    def test_exact_workflow_attempt_source_and_sha_required(self):
        for key, value in (
            ("id", int(RUN) + 1), ("run_attempt", 2),
            ("event", "push"), ("head_branch", "develop"),
            ("repository", {"full_name": "evil/repo"}),
            ("path", ".github/workflows/something-else.yml"),
            ("head_sha", "not-a-sha"),
            ("status", "unknown"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                verified_state(RUN, "1", {**LIVE, key: value})
        with self.assertRaises(ValueError):
            verified_state(RUN, "1", dict(LIVE, conclusion="success"))
        with self.assertRaises(ValueError):
            verified_state(RUN, "1", dict(LIVE, status="completed", conclusion=None))
        with self.assertRaises(ValueError):
            verified_state(RUN, "2", LIVE)
        self.assertEqual(verified_state(RUN, "1", LIVE)["state"], "nonterminal_at_github")
        self.assertFalse(verified_state(RUN, "1", LIVE)["telemetry_proves_liveness"])
        queued = verified_state(RUN, "1", dict(LIVE, status="queued"))
        self.assertEqual(queued["state"], "nonterminal_at_github")
        self.assertEqual(queued["github_status"], "queued")

    def test_proposal_never_executes_and_binds_to_exact_run(self):
        from oc_fleet_observe import read_only
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.db"
            store = Store(path)
            store.register(RUN, "1", "OAS-12")
            before = path.read_bytes()
            with read_only(path) as db:
                item = snapshot(db, RUN, "1", 0, fetcher=lambda _: LIVE)
            when = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
            prop = cancel_proposal(item, when)
            second = cancel_proposal(item, when)
            self.assertEqual(prop, second)
            self.assertFalse(prop["executable"])
            self.assertEqual(prop["approval"], "required_not_granted")
            self.assertEqual(prop["manifest_task_identity"], "not_verified")
            self.assertEqual(prop["head_sha"], SHA)
            self.assertEqual(prop["expires_at_utc"], "2026-10-21T20:05:00Z")
            self.assertEqual(prop["registered_multica_issue"], "OAS-12")
            self.assertEqual(prop["action"], "propose_cancel")
            self.assertEqual(path.read_bytes(), before)

    def test_gh_read_only_and_fail_closed_errors(self):
        fake = json.dumps(LIVE)
        from unittest.mock import Mock
        good = Mock(stdout=fake)
        with patch("oc_fleet_supervisor.subprocess.run", return_value=good) as op:
            self.assertEqual(get_github_run(RUN)["id"], int(RUN))
            args = op.call_args.args[0]
            self.assertEqual(args, ["gh", "api",
                                   "repos/oaslananka-dev/oc-test/actions/runs/" + RUN])
            self.assertNotIn("-X", args)
        with patch("oc_fleet_supervisor.subprocess.run", side_effect=OSError()):
            with self.assertRaises(OSError):
                get_github_run(RUN)
        with self.assertRaises(ValueError):
            get_github_run(RUN + "/cancel")


if __name__ == "__main__":
    unittest.main()
