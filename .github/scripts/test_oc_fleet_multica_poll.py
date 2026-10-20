#!/usr/bin/env python3
"""Offline tests for bounded, idempotent Multica task polling."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import oc_fleet_multica_poll as poller

PROJECT = "0506e0e5-01a7-4de9-b63b-67e993809721"
META = {
    "fleet_target_repo": "oaslananka-dev/oc-test",
    "fleet_target_issue": "53",
    "fleet_worker_repo": "MakerParsDev/oc-test",
    "fleet_worker_account": "MakerParsDev",
    "fleet_target_account": "oaslananka",
    "fleet_allowed_path": "playground/fleet/supplemental-receipt.json",
    "fleet_ready": True,
}
ISSUE = {"identifier":"OAS-14", "status":"todo", "project_id":PROJECT,
         "assignee_type":None, "metadata":META}


class PollTests(unittest.TestCase):
    def test_project_scoped_filtered_list(self):
        with patch.object(poller.subprocess,"run",
                          return_value=MagicMock(stdout='{"has_more":false,"issues":[]}')) as call:
            self.assertEqual(poller.ready_issues(PROJECT), [])
            cmd=call.call_args.args[0]
            self.assertIn(PROJECT, cmd)
            self.assertIn("fleet_ready=true", cmd)

    def test_reject_incomplete_result(self):
        with patch.object(poller.subprocess,"run",
                          return_value=MagicMock(stdout='{"has_more":true,"issues":[]}')):
            with self.assertRaisesRegex(ValueError,"Incomplete"):
                poller.ready_issues(PROJECT)

    def test_no_eligible_work_is_inert(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(poller,"ready_issues",return_value=[]),                  patch.object(poller.handoff,"read_issue") as read:
                self.assertEqual(poller.poll(PROJECT,True,Path(temp)),"empty")
                read.assert_not_called()

    def test_plan_does_not_create_claim_or_start_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),                  patch.object(poller.handoff,"read_issue",return_value=ISSUE),                  patch.object(poller,"controller_receipt",return_value=root/"missing"),                  patch.object(poller.subprocess,"run") as run:
                self.assertEqual(poller.poll(PROJECT,False,root),"planned")
                run.assert_not_called()
                self.assertEqual(list(root.iterdir()),[])

    def test_existing_receipt_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            receipt=root/"evidence.json";receipt.write_text("{}")
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),                  patch.object(poller.handoff,"read_issue",return_value=ISSUE),                  patch.object(poller,"controller_receipt",return_value=receipt),                  patch.object(poller.subprocess,"run") as run:
                self.assertEqual(poller.poll(PROJECT,True,root),"no_new_work")
                run.assert_not_called()

    def test_failed_dispatch_keeps_claim_and_never_changes_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),                  patch.object(poller.handoff,"read_issue",return_value=ISSUE),                  patch.object(poller,"controller_receipt",return_value=root/"missing"),                  patch.object(poller.subprocess,"run",
                              return_value=MagicMock(returncode=1,stdout="")) as run:
                with self.assertRaisesRegex(ValueError,"claim retained"):
                    poller.poll(PROJECT,True,root)
                self.assertTrue((root/"OAS-14.claim").is_file())
                self.assertEqual(run.call_count,1)
                self.assertEqual(poller.poll(PROJECT,True,root),"no_new_work")
                self.assertEqual(run.call_count,1)

    def test_successful_dispatch_marks_for_review_and_blocks_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            published='{"status": "PR_CREATED", "run_id": "123", "url":"https://example.invalid"}'
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),                  patch.object(poller.handoff,"read_issue",return_value=ISSUE),                  patch.object(poller,"controller_receipt",return_value=root/"missing"),                  patch.object(poller.subprocess,"run",
                              return_value=MagicMock(returncode=0,stdout=published)) as run:
                self.assertEqual(poller.poll(PROJECT,True,root),"dispatched")
                self.assertEqual(run.call_count,5)
                calls=[c.args[0] for c in run.call_args_list]
                self.assertIn("--worker-repo",calls[0])
                self.assertIn("fleet_ready",calls[1])
                self.assertIn("in_review",calls[-1])
                self.assertEqual(poller.poll(PROJECT,True,root),"no_new_work")
                self.assertEqual(run.call_count,5)


if __name__ == "__main__":
    unittest.main()
