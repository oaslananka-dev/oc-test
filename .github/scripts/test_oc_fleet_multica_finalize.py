#!/usr/bin/env python3
"""Offline checks of scoped, opt-in CI acceptance and Multica closure."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import oc_fleet_multica_finalize as finish

PROJECT = "0506e0e5-01a7-4de9-b63b-67e993809721"
META = {
 "fleet_target_repo":"oaslananka-dev/oc-test",
 "fleet_worker_repo":"MakerParsDev/oc-test",
 "fleet_target_issue":"59",
 "fleet_allowed_path":"playground/fleet/acceptance.json",
 "fleet_run_id":"39350000123",
 "fleet_pull_request":"60",
 "fleet_auto_merge":True,
 "fleet_ready":False,
 "fleet_result":"pr_created",
}
ISSUE = {"identifier":"OAS-16","project_id":PROJECT,"status":"in_review",
         "assignee_type":None,"metadata":META}
RECEIPT = {"worker_repo":META["fleet_worker_repo"],
 "run_id":int(META["fleet_run_id"]),
 "manifest":{"repository":META["fleet_target_repo"],
             "issue_key":"GH-59","allowed_paths":[META["fleet_allowed_path"]]}}
HEAD="a"*40
PR={"number":60,"state":"open","merged":False,"draft":False,
    "head":{"sha":HEAD,"repo":{"full_name":finish.PILOT_REPO}},
    "base":{"ref":"main","repo":{"full_name":finish.PILOT_REPO}},
    "body":"Closes #59","merge_commit_sha":None}


class CompleteTests(unittest.TestCase):
    def test_receipt_must_match_run_repo_issue_and_paths(self):
        finish.verify_receipt(META,RECEIPT)
        for key,bad in (
            ("worker_repo","SomeoneElse/worker"),
            ("run_id",321),
            ("manifest",{"repository":finish.PILOT_REPO,"issue_key":"GH-50",
                         "allowed_paths":[META["fleet_allowed_path"]]}),
        ):
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,"receipt"):
                finish.verify_receipt(META,{**RECEIPT,key:bad})

    def test_ci_checks_require_both_actual_successful_results(self):
        base={"headRefOid":HEAD,"mergeStateStatus":"CLEAN","reviewDecision":"",
              "statusCheckRollup":[
                {"name":"baseline","status":"COMPLETED","conclusion":"SUCCESS"},
                {"name":"semgrep-cloud-platform/scan","status":"COMPLETED","conclusion":"SUCCESS"}]}
        with patch.object(finish,"run",return_value=json.dumps(base)):
            self.assertTrue(finish.checks_ok(finish.PILOT_REPO,60,HEAD))
        for bad in (
            {**base,"headRefOid":"b"*40},
            {**base,"mergeStateStatus":"BLOCKED"},
            {**base,"reviewDecision":"CHANGES_REQUESTED"},
            {**base,"statusCheckRollup":base["statusCheckRollup"][:1]},
            {**base,"statusCheckRollup":[*base["statusCheckRollup"][:1],
                {"name":"semgrep-cloud-platform/scan","status":"COMPLETED","conclusion":"SKIPPED"}]},
            {**base,"statusCheckRollup":[*base["statusCheckRollup"][:1],
                {"name":"semgrep-cloud-platform/scan","status":"IN_PROGRESS","conclusion":None}]},
        ):
            with patch.object(finish,"run",return_value=json.dumps(bad)):
                self.assertFalse(finish.checks_ok(finish.PILOT_REPO,60,HEAD))

    def test_pr_file_scope_and_issue_binding(self):
        scoped=[{"filename":META["fleet_allowed_path"],"status":"added"}]
        with patch.object(finish,"gh",side_effect=[PR,scoped]):
            self.assertEqual(finish.review_pr(META)["number"],60)
        with patch.object(finish,"gh",side_effect=[PR,[{"filename":".github/workflows/x.yml","status":"added"}]]):
            with self.assertRaisesRegex(ValueError,"scope"):
                finish.review_pr(META)
        with patch.object(finish,"gh",side_effect=[{**PR,"body":"Closes #58"},scoped]):
            with self.assertRaisesRegex(ValueError,"intended"):
                finish.review_pr(META)

    def test_bot_review_comments_block_merge(self):
        with patch.object(finish,"gh",side_effect=[
            [{"state":"APPROVED"}],[{"body":"Please fix"}],[]]):
            self.assertFalse(finish.comments_clear(finish.PILOT_REPO,60))

    def test_not_explicitly_opted_in_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"receipt.json";path.write_text(json.dumps(RECEIPT))
            with patch.object(finish.handoff,"read_issue",return_value={
                **ISSUE,"metadata":{**META,"fleet_auto_merge":False}}), \
                patch.object(finish,"run") as run:
                self.assertEqual(finish.finalize_one(ISSUE,PROJECT,True,lambda _:path),
                                 "not_authorized")
                run.assert_not_called()

    def test_wait_for_ci_never_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"receipt.json";path.write_text(json.dumps(RECEIPT))
            with patch.object(finish.handoff,"read_issue",return_value=ISSUE), \
                patch.object(finish,"review_pr",return_value=PR), \
                patch.object(finish,"checks_ok",return_value=False), \
                patch.object(finish,"run") as run:
                self.assertEqual(finish.finalize_one(ISSUE,PROJECT,True,lambda _:path),
                                 "waiting_ci_or_review")
                run.assert_not_called()

    def test_success_merges_pinned_head_and_completes_multica(self):
        merged={**PR,"merged":True,"state":"closed","merge_commit_sha":"c"*40}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"receipt.json";path.write_text(json.dumps(RECEIPT))
            with patch.object(finish.handoff,"read_issue",return_value=ISSUE), \
                patch.object(finish,"review_pr",side_effect=[PR,merged]), \
                patch.object(finish,"checks_ok",return_value=True), \
                patch.object(finish,"comments_clear",return_value=True), \
                patch.object(finish,"gh",side_effect=[{"state":"closed"},{"type":"file"}]), \
                patch.object(finish,"run",return_value="") as run:
                self.assertEqual(finish.finalize_one(ISSUE,PROJECT,True,lambda _:path),
                                 "completed")
                self.assertEqual(run.call_count,4)
                merge=run.call_args_list[0].args
                self.assertEqual(merge[:4],("gh","pr","merge","60"))
                self.assertIn("--match-head-commit",merge)
                self.assertIn(HEAD,merge)
                self.assertEqual(run.call_args_list[-1].args[-3:],("OAS-16","done","--no-start"))

    def test_merged_pr_reconciles_without_second_merge(self):
        merged={**PR,"merged":True,"state":"closed","merge_commit_sha":"c"*40}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"receipt.json";path.write_text(json.dumps(RECEIPT))
            with patch.object(finish.handoff,"read_issue",return_value=ISSUE), \
                patch.object(finish,"review_pr",return_value=merged), \
                patch.object(finish,"gh",side_effect=[{"state":"closed"},{"type":"file"}]), \
                patch.object(finish,"run",return_value="") as run:
                self.assertEqual(finish.finalize_one(ISSUE,PROJECT,True,lambda _:path),
                                 "completed")
                self.assertEqual(run.call_count,3)


if __name__ == "__main__":
    unittest.main()
