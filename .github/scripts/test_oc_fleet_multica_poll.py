#!/usr/bin/env python3
"""Offline tests for bounded, idempotent Multica task polling."""
import tempfile
from threading import Barrier, Lock
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import oc_fleet_multica_poll as poller
REAL_WORKER_SLOTS=poller.worker_slots

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
    def setUp(self):
        # Legacy tests exercise dispatch, not external GitHub Actions admission.
        self.capacity=patch.object(poller,"worker_slots",
                                   side_effect=lambda repo,budget:budget)
        self.capacity.start()
        self.addCleanup(self.capacity.stop)

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
            published='{"status": "PR_CREATED", "run_id": "123", "url":"https://github.com/oaslananka-dev/oc-test/pull/1"}'
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),                  patch.object(poller.handoff,"read_issue",return_value=ISSUE),                  patch.object(poller,"controller_receipt",return_value=root/"missing"),                  patch.object(poller.subprocess,"run",
                              return_value=MagicMock(returncode=0,stdout=published)) as run:
                self.assertEqual(poller.poll(PROJECT,True,root),"dispatched")
                self.assertEqual(run.call_count,6)
                calls=[c.args[0] for c in run.call_args_list]
                self.assertIn("--worker-repo",calls[0])
                self.assertIn("fleet_ready",calls[1])
                self.assertIn("in_review",calls[-1])
                self.assertEqual(poller.poll(PROJECT,True,root),"no_new_work")
                self.assertEqual(run.call_count,6)


    def test_two_controllers_actually_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            other={**ISSUE, "identifier":"OAS-17",
                   "metadata":{**META,"fleet_target_issue":"61"}}
            issues=[ISSUE,other]
            barrier=Barrier(2,timeout=3)
            starts=[]
            lock=Lock()
            def simultaneous(item, project, execute, claims):
                with lock:
                    starts.append(item["identifier"])
                barrier.wait()
                return "dispatched"
            with patch.object(poller,"ready_issues",return_value=issues), \
                 patch.object(poller.handoff,"read_issue",side_effect=lambda k: next(x for x in issues if x["identifier"]==k)), \
                 patch.object(poller,"controller_receipt",side_effect=lambda m:root/(m["fleet_target_issue"]+".json")), \
                 patch.object(poller,"dispatch_one",side_effect=simultaneous):
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=2),"dispatched_parallel")
                self.assertCountEqual(starts,["OAS-14","OAS-17"])

    def test_same_target_is_never_dispatched_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            other={**ISSUE,"identifier":"OAS-17"}
            issues=[ISSUE,other]
            with patch.object(poller,"ready_issues",return_value=issues), \
                 patch.object(poller.handoff,"read_issue",side_effect=lambda k: next(x for x in issues if x["identifier"]==k)), \
                 patch.object(poller,"controller_receipt",return_value=root/"none"), \
                 patch.object(poller,"dispatch_one",return_value="dispatched") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=2),"dispatched")
                dispatch.assert_called_once()

    def test_parallel_failure_waits_for_peer_and_reports_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            other={**ISSUE,"identifier":"OAS-17","metadata":{**META,"fleet_target_issue":"61"}}
            issues=[ISSUE,other]
            processed=[]
            def handle(item,*_):
                processed.append(item["identifier"])
                if item["identifier"]=="OAS-14":
                    raise ValueError("failed with claim preserved")
                return "dispatched"
            with patch.object(poller,"ready_issues",return_value=issues), \
                 patch.object(poller.handoff,"read_issue",side_effect=lambda k: next(x for x in issues if x["identifier"]==k)), \
                 patch.object(poller,"controller_receipt",side_effect=lambda m:root/(m["fleet_target_issue"]+".json")), \
                 patch.object(poller,"dispatch_one",side_effect=handle):
                with self.assertRaisesRegex(ValueError,"OAS-14"):
                    poller.poll(PROJECT,True,root,max_parallel=2)
                self.assertCountEqual(processed,["OAS-14","OAS-17"])

    def test_parallel_capacity_rejects_out_of_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            for count in (0,21):
                with self.assertRaisesRegex(ValueError,"1..20"):
                    poller.poll(PROJECT,True,Path(temp),max_parallel=count)

    def test_full_worker_pool_retains_queue_without_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),patch.object(
                poller.handoff,"read_issue",return_value=ISSUE),patch.object(
                poller,"controller_receipt",return_value=root/"none"),patch.object(
                poller,"worker_slots",return_value=0),patch.object(
                poller,"dispatch_one") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=4),"no_capacity")
                dispatch.assert_not_called()
                self.assertFalse((root/"OAS-14.claim").exists())

    def test_admit_four_distinct_targets_in_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            entries=[{**ISSUE,"identifier":f"OAS-{i}","metadata":{
                **META,"fleet_target_issue":str(100+i)}}
                for i in range(30,34)]
            with patch.object(poller,"ready_issues",return_value=entries),patch.object(
                poller.handoff,"read_issue",side_effect=lambda k:next(
                    x for x in entries if x["identifier"]==k)),patch.object(
                poller,"controller_receipt",side_effect=lambda m:root/(m["fleet_target_issue"]+".json")),patch.object(
                poller,"worker_slots",return_value=4) as capacity,patch.object(
                poller,"dispatch_one",return_value="dispatched") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=4),"dispatched_parallel")
                self.assertEqual(dispatch.call_count,4)
                capacity.assert_called_once_with("MakerParsDev/oc-test",4)

    def test_worker_slots_exactly_counts_busy_workflow_runs(self):
        sample=lambda total:MagicMock(stdout=__import__("json").dumps({
            "total_count":total,
            "workflow_runs":[{"status":"in_progress"} for _ in range(total)]}))
        with patch.object(poller.subprocess,"run",side_effect=[
            sample(1),sample(1),sample(0),sample(0)]) as gh:
            self.assertEqual(REAL_WORKER_SLOTS("MakerParsDev/oc-test",4),2)
            self.assertEqual(gh.call_count,4)
        with patch.object(poller.subprocess,"run",return_value=MagicMock(
            stdout='{"total_count":101,"workflow_runs":[]}')):
            with self.assertRaisesRegex(ValueError,"Incomplete"):
                REAL_WORKER_SLOTS("MakerParsDev/oc-test",4)


if __name__ == "__main__":
    unittest.main()
