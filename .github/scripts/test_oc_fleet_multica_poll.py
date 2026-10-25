#!/usr/bin/env python3
"""Offline tests for bounded, idempotent Multica task polling."""
import tempfile
from threading import Barrier, Lock
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import oc_fleet_multica_poll as poller
REAL_WORKER_SLOTS=poller.worker_slots
REAL_OCCUPIED_SCOPES=poller.occupied_file_scopes
REAL_OPEN_GITHUB_PR_SCOPES=poller.open_github_pr_file_scopes

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
        self.global_budget = patch("oc_fleet_pool.snapshot", return_value={
            "global_free": 4, "per_account_limit": 4})
        self.global_budget.start()
        self.addCleanup(self.global_budget.stop)
        self.scopes=patch.object(poller,"occupied_file_scopes",return_value=[])
        self.scopes_mock=self.scopes.start()
        self.addCleanup(self.scopes.stop)
        self.open_prs=patch.object(poller,"open_github_pr_file_scopes",return_value=[])
        self.open_prs_mock=self.open_prs.start()
        self.addCleanup(self.open_prs.stop)

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

    def test_global_four_worker_budget_blocks_other_account(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            second=dict(ISSUE)
            second["metadata"]={**META,
                "fleet_worker_repo":"boardreadyops/oc-test",
                "fleet_worker_account":"boardreadyops"}
            with patch.object(poller,"ready_issues",return_value=[second]),                 patch.object(poller.handoff,"read_issue",return_value=second),                 patch.object(poller,"controller_receipt",return_value=root/"missing"),                 patch("oc_fleet_pool.snapshot",return_value={
                   "global_free":0,"per_account_limit":4}),                 patch.object(poller,"dispatch_one") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root),"no_capacity")
                dispatch.assert_not_called()
                self.assertEqual(list(root.iterdir()),[])

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
                self.scopes_mock.assert_not_called()
                self.open_prs_mock.assert_not_called()
                self.assertEqual(list(root.iterdir()),[])

    def test_existing_receipt_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            receipt=root/"evidence.json";receipt.write_text("{}")
            with patch.object(poller,"ready_issues",return_value=[ISSUE]),                  patch.object(poller.handoff,"read_issue",return_value=ISSUE),                  patch.object(poller,"controller_receipt",return_value=receipt),                  patch.object(poller.subprocess,"run") as run:
                self.assertEqual(poller.poll(PROJECT,True,root),"no_new_work")
                run.assert_not_called()
                self.scopes_mock.assert_not_called()
                self.open_prs_mock.assert_not_called()

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
                   "metadata":{**META,"fleet_target_issue":"61",
                               "fleet_allowed_path":"playground/fleet/other.json"}}
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

    def test_distinct_issues_same_file_are_serialized(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            second={**ISSUE,"identifier":"OAS-17","metadata":{
                **META,"fleet_target_issue":"61"}}
            tasks=[ISSUE,second]
            with patch.object(poller,"ready_issues",return_value=tasks), patch.object(
                poller.handoff,"read_issue",side_effect=lambda k:next(
                    t for t in tasks if t["identifier"]==k)), patch.object(
                poller,"controller_receipt",side_effect=lambda m:
                    root/(m["fleet_target_issue"]+".json")), patch.object(
                poller,"dispatch_one",return_value="dispatched") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=4),"dispatched")
                dispatch.assert_called_once()
                self.assertEqual(dispatch.call_args.args[0]["identifier"],"OAS-14")

    def test_open_bot_pr_blocks_distinct_multica_issue_on_same_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            task=ISSUE
            open_scope=("oaslananka-dev/oc-test",
                        ("playground/fleet/supplemental-receipt.json",))
            with patch.object(poller,"ready_issues",return_value=[task]),patch.object(
                poller.handoff,"read_issue",return_value=task),patch.object(
                poller,"controller_receipt",return_value=root/"missing"),patch.object(
                poller,"open_github_pr_file_scopes",return_value=[open_scope]) as github,patch.object(
                poller,"dispatch_one") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=4),
                                 "no_new_work")
                github.assert_called_once_with("oaslananka-dev/oc-test")
                dispatch.assert_not_called()
                self.assertFalse(list(root.iterdir()))

    def test_open_github_pr_query_is_once_per_repo_for_four_disjoint_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            tasks=[{**ISSUE,"identifier":f"OAS-{i}","metadata":{
                **META,"fleet_target_issue":str(i),
                "fleet_allowed_path":f"playground/fleet/independent-{i}.json"}}
                for i in (50,51,52,53)]
            with patch.object(poller,"ready_issues",return_value=tasks),patch.object(
                poller.handoff,"read_issue",side_effect=lambda k:next(
                    t for t in tasks if t["identifier"]==k)),patch.object(
                poller,"controller_receipt",side_effect=lambda m:
                    root/(m["fleet_target_issue"]+".json")),patch.object(
                poller,"open_github_pr_file_scopes",return_value=[]) as github,patch.object(
                poller,"dispatch_one",return_value="dispatched") as dispatched:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=4),
                                 "dispatched_parallel")
                github.assert_called_once_with("oaslananka-dev/oc-test")
                self.assertEqual(dispatched.call_count,4)

    def test_open_pr_graphql_fails_closed_on_pagination_and_bad_paths(self):
        import json
        def sample(files,more=False,pr_more=False):
            return {"data":{"repository":{"pullRequests":{
                "pageInfo":{"hasNextPage":pr_more},
                "nodes":[{"files":{"pageInfo":{"hasNextPage":more},
                                   "nodes":[{"path":path} for path in files]}}]}}}}
        for payload in (
            sample(["safe/file.txt"],pr_more=True),
            sample(["safe/file.txt"],more=True),
            sample(["../bad.txt"]),
            sample(["bad//path"]),
            sample(["/rooted"]),
            {"errors":[{"message":"Forbidden"}]},
            {"data":{"repository":None}},
        ):
            with self.subTest(payload=payload),patch.object(
                poller.subprocess,"run",
                return_value=MagicMock(stdout=json.dumps(payload))):
                with self.assertRaises(ValueError):
                    REAL_OPEN_GITHUB_PR_SCOPES("oaslananka/fovux-kit")

    def test_graphql_real_shape_returns_case_normalized_exact_paths(self):
        import json
        data={"data":{"repository":{"pullRequests":{
            "pageInfo":{"hasNextPage":False},"nodes":[
              {"files":{"pageInfo":{"hasNextPage":False},"nodes":[
                  {"path":".github/labels.yml"},
                  {"path":"fovux-studio/PNPM-lock.yaml"},
                  {"path":"fovux-studio/package.json"}]}},
              {"files":{"pageInfo":{"hasNextPage":False},"nodes":[
                  {"path":"fovux-mcp/uv.lock"}]}}
        ]}}}}
        with patch.object(poller.subprocess,"run",return_value=MagicMock(
                stdout=json.dumps(data))) as cmd:
            scopes=REAL_OPEN_GITHUB_PR_SCOPES("oaslananka/fovux-kit")
            self.assertEqual(scopes,[
                ("oaslananka/fovux-kit",
                 (".github/labels.yml","fovux-studio/pnpm-lock.yaml",
                  "fovux-studio/package.json")),
                ("oaslananka/fovux-kit",("fovux-mcp/uv.lock",))])
            self.assertEqual(cmd.call_args.args[0][:3],["gh","api","graphql"])

    def test_directory_file_overlap_and_distinct_repos(self):
        a=("oaslananka/fovux-kit",("fovux-studio/",))
        self.assertTrue(poller.file_scopes_overlap(
            a,("oaslananka/fovux-kit",("fovux-studio/pnpm-lock.yaml",))))
        self.assertFalse(poller.file_scopes_overlap(
            a,("oaslananka/fovux-kit",("fovux-mcp/uv.lock",))))
        self.assertFalse(poller.file_scopes_overlap(
            a,("different/repo",("fovux-studio/pnpm-lock.yaml",))))
        self.assertFalse(poller.file_scopes_overlap(
            ("repo/name",("fovux-studio/pnpm-lock.yaml",)),
            ("repo/name",("fovux-studio/pnpm-lock.yaml.bak",))))

    def test_running_or_in_review_scopes_skip_overlapping_new_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            src={**ISSUE,"metadata":{**META,"fleet_allowed_path":
                "fovux-studio/pnpm-lock.yaml"}}
            held=("oaslananka-dev/oc-test",("fovux-studio/",))
            with patch.object(poller,"ready_issues",return_value=[src]),patch.object(
                poller.handoff,"read_issue",return_value=src),patch.object(
                poller,"occupied_file_scopes",return_value=[held]),patch.object(
                poller,"controller_receipt",return_value=root/"missing"),patch.object(
                poller,"dispatch_one") as dispatch:
                self.assertEqual(poller.poll(PROJECT,True,root,max_parallel=4),
                                 "no_new_work")
                dispatch.assert_not_called()
                self.assertFalse(list(root.iterdir()))

    def test_partial_active_inventory_fails_closed(self):
        with patch.object(poller.subprocess,"run",return_value=MagicMock(
                stdout='{"has_more":true,"issues":[]}')):
            with self.assertRaisesRegex(ValueError,"Incomplete active"):
                REAL_OCCUPIED_SCOPES(PROJECT)

    def test_active_scope_inventory_reserves_workers_and_review_prs(self):
        import json
        active=[
            {"status":"in_progress","metadata":{**META,
             "fleet_result":"worker_dispatched","fleet_allowed_path":"fovux-mcp/uv.lock"}},
            {"status":"in_review","metadata":{**META,
             "fleet_result":"pr_created","fleet_allowed_path":"fovux-studio/pnpm-lock.yaml"}},
            {"status":"blocked","metadata":{**META,
             "fleet_result":"worker_failed","fleet_allowed_path":"no-reservation.txt"}},
        ]
        with patch.object(poller.subprocess,"run",return_value=MagicMock(
                stdout=json.dumps({"has_more":False,"issues":active}))) as call:
            paths=REAL_OCCUPIED_SCOPES(PROJECT)
            self.assertEqual(len(paths),2)
            self.assertEqual(paths[0][1],("fovux-mcp/uv.lock",))
            self.assertEqual(paths[1][1],("fovux-studio/pnpm-lock.yaml",))
            self.assertIn("--project",call.call_args.args[0])

    def test_parallel_failure_waits_for_peer_and_reports_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            other={**ISSUE,"identifier":"OAS-17","metadata":{**META,"fleet_target_issue":"61",
                               "fleet_allowed_path":"playground/fleet/other.json"}}
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
                **META,"fleet_target_issue":str(100+i),
                "fleet_allowed_path":f"playground/fleet/task-{i}.json"}}
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

    def test_eight_global_workers_never_exceeds_four_on_one_account(self):
        first=dict(ISSUE)
        one=first["metadata"]["fleet_worker_repo"]
        second=dict(ISSUE)
        second["identifier"]="OAS-66"
        second["metadata"]={**first["metadata"],
              "fleet_target_issue":"66",
              "fleet_allowed_path":"source/second.py",
              "fleet_worker_repo":"boardreadyops/oc-test",
              "fleet_worker_account":"boardreadyops"}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(poller,"ready_issues",return_value=[first,second]),                 patch.object(poller.handoff,"read_issue",
                     side_effect=lambda key:first if key=="OAS-14" else second),                 patch.object(poller,"controller_receipt",
                     side_effect=lambda meta:Path(td)/"no-receipt"),                 patch("oc_fleet_pool.snapshot",return_value={
                     "global_free":8,"per_account_limit":4}),                 patch.object(poller,"worker_slots",return_value=4) as slots,                 patch.object(poller,"dispatch_one",return_value="dispatched") as dispatch:
                poller.poll(PROJECT,True,Path(td),max_parallel=8)
                self.assertTrue(all(args.args[1]==4 for args in slots.call_args_list))
                self.assertLessEqual(dispatch.call_count,2)

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
