"""Read-only OSV candidate detection with complete CI/run/advisory evidence."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_osv_discover as discover
import oc_fleet_osv_publish as publish
from test_oc_fleet_osv_lockfile import SARIF, ADVISORY, BEFORE, FINDING

SHA="a"*40
RUN=123456789
PR={
 "number":173,"state":"open","draft":False,"user":{"login":"oaslananka"},
 "head":{"sha":SHA,"ref":"fix/osv","repo":{"full_name":discover.REPO}},
 "base":{"ref":"main","repo":{"full_name":discover.REPO}},
}
def check(name,id,result):
    return {"name":name,"id":id,"conclusion":result,"status":"completed",
            "head_sha":SHA,"app":{"slug":"github-actions"},
            "details_url":f"https://github.com/{discover.REPO}/actions/runs/{RUN}/job/{id}"}
CHECKS={
 "total_count":4,"check_runs":[
  check(publish.CHECK_OSV,200,"failure"),
  check(publish.CHECK_AGGREGATE,201,"failure"),
  check("Node.js (24)",202,"success"),
  check("Optional skipped",203,"skipped"),
 ]}
FILES=[{"filename":"node/package-lock.json","status":"modified"}]
RUN_INFO={
 "id":RUN,"head_sha":SHA,"head_branch":"fix/osv",
 "event":"pull_request","name":"Dependency Audit","conclusion":"failure"}
ARTIFACTS={"total_count":1,"artifacts":[{
 "name":publish.ARTIFACT,"expired":False,"id":1000}]}
LOCK_RESPONSE={
 "type":"file","encoding":"base64",
 "size":len(json.dumps(BEFORE).encode()),
 "content":base64.b64encode(json.dumps(BEFORE).encode()).decode(),
}


class SelectionTests(unittest.TestCase):
    def test_shape_owner_same_repo_and_immutable_sha(self):
        self.assertTrue(discover.pr_shape(PR))
        for override in (
            {"user":{"login":"someone"}},{"state":"closed"},{"draft":True},
            {"head":{"sha":SHA,"ref":"fix/osv","repo":None}},
            {"base":{"ref":"main","repo":{"full_name":"some/repo"}}},
            {"number":"173"},
        ):
            with self.subTest(override=override):
                self.assertFalse(discover.pr_shape({**PR,**override}))

    def test_github_job_bound_to_single_workflow_and_success_set(self):
        with patch.object(discover.github,"gh",return_value=deepcopy(CHECKS)):
            run,ids,required=discover.completed_checks({},SHA)
        self.assertEqual(run,RUN)
        self.assertEqual(ids,{publish.CHECK_OSV:200,publish.CHECK_AGGREGATE:201})
        self.assertEqual(set(required),{publish.CHECK_OSV,publish.CHECK_AGGREGATE,"Node.js (24)"})
        self.assertNotIn("Optional skipped",required)

    def test_wrong_or_pending_checks_cannot_generate_policy(self):
        cases=[]
        x=deepcopy(CHECKS);x["check_runs"][0]["details_url"]="https://other/run/1"
        cases.append(x)
        x=deepcopy(CHECKS);x["check_runs"][1]["details_url"]=x["check_runs"][1]["details_url"].replace(
            str(RUN),str(RUN+1))
        cases.append(x)
        x=deepcopy(CHECKS);x["check_runs"][2]["status"]="in_progress"
        cases.append(x)
        x=deepcopy(CHECKS);x["check_runs"][2]["conclusion"]="failure"
        cases.append(x)
        x=deepcopy(CHECKS);x["total_count"]=5
        cases.append(x)
        x=deepcopy(CHECKS);x["check_runs"][3]["name"]="Node.js (24)"
        cases.append(x)
        x=deepcopy(CHECKS);x["check_runs"][2]["head_sha"]="b"*40
        cases.append(x)
        for item in cases:
            with self.subTest(item=item),patch.object(discover.github,"gh",return_value=item):
                with self.assertRaises(ValueError):
                    discover.completed_checks({},SHA)

    def test_one_open_pr_only_modified_lockfile(self):
        extra=FILES + [{"filename":"README.md","status":"modified"}]
        with patch.object(discover.github,"gh",return_value=extra):
            self.assertIsNone(discover.proposed_pr_policy({},PR))
        with patch.object(discover.github,"gh",return_value=[{
            "filename":discover.osv.LOCK,"status":"added"}]):
            self.assertIsNone(discover.proposed_pr_policy({},PR))

    def test_full_read_only_candidate_uses_existing_publisher_authority(self):
        responses=[
            FILES, CHECKS, RUN_INFO, ARTIFACTS,
            LOCK_RESPONSE,
        ]
        def download(target,policy,folder):
            self.assertEqual(policy["workflow_run_id"],RUN)
            return deepcopy(SARIF)
        with patch.object(discover.github,"gh",side_effect=responses),patch.object(
            discover.publisher,"fetch_sarif",side_effect=download),patch.object(
            discover.osv,"gh_advisory",return_value=ADVISORY),patch.object(
            discover.publisher,"check_current",return_value=ARTIFACTS) as final:
            policy=discover.proposed_pr_policy({},PR)
        self.assertEqual(policy["advisory_id"],FINDING["ghsa"])
        self.assertEqual(policy["head_sha"],SHA)
        self.assertEqual(set(policy["required_green_checks"]),
                         {publish.CHECK_OSV,publish.CHECK_AGGREGATE,"Node.js (24)"})
        final.assert_called_once()

    def test_wrong_sarif_refused_without_publishing(self):
        item=deepcopy(SARIF)
        item["runs"][0]["results"][0]["ruleId"]="CVE-2026-0000"
        with patch.object(discover.github,"gh",side_effect=[
            FILES,CHECKS,RUN_INFO,ARTIFACTS]),patch.object(
            discover.publisher,"fetch_sarif",return_value=item),patch.object(
            discover.publisher,"publish") as pub:
            with self.assertRaises(ValueError):
                discover.proposed_pr_policy({},PR)
            pub.assert_not_called()

    def test_latest_main_pending_blocks_older_success_same_sha(self):
        runs=[
          {"head_sha":SHA,"event":"push","status":"in_progress","id":500},
          {"head_sha":SHA,"event":"push","status":"completed","conclusion":"success","id":499},
        ]
        with patch.object(discover.github,"gh",side_effect=[
            {"commit":{"sha":SHA}},
            {"total_count":2,"workflow_runs":runs},
            {"commit":{"sha":SHA}},
        ]):
            result=discover.last_main_audit({})
        self.assertEqual(result["state"],"MAIN_AUDIT_PENDING")
        self.assertEqual(result["workflow_run_id"],500)

    def test_latest_main_success_or_failure(self):
        for conclusion,state in (("success","MAIN_AUDIT_GREEN"),
                                 ("failure","MAIN_AUDIT_FAILED_REVIEW")):
            with self.subTest(conclusion=conclusion),patch.object(
                discover.github,"gh",side_effect=[
                    {"commit":{"sha":SHA}},
                    {"total_count":1,"workflow_runs":[{
                        "head_sha":SHA,"event":"push","status":"completed",
                        "conclusion":conclusion,"id":500}]},
                    {"commit":{"sha":SHA}},
                ]):
                found=discover.last_main_audit({})
                self.assertEqual(found["state"],state)
                self.assertNotIn("advisory_id",found)

    def test_main_head_changed_during_read_cannot_be_reported_green(self):
        with patch.object(discover.github,"gh",side_effect=[
            {"commit":{"sha":SHA}},
            {"total_count":1,"workflow_runs":[{
                "head_sha":SHA,"event":"push","status":"completed",
                "conclusion":"success","id":500}]},
            {"commit":{"sha":"b"*40}},
        ]):
            observed=discover.last_main_audit({})
        self.assertEqual(observed["state"],"MAIN_HEAD_MOVED")
        self.assertEqual(observed["old_sha"],SHA)
        self.assertEqual(observed["main_sha"],"b"*40)

    def test_full_history_page_without_sha_match_is_not_a_green_or_empty_assertion(self):
        history=[{"head_sha":"b"*40,"event":"push","status":"completed",
                  "conclusion":"success","id":500-i} for i in range(10)]
        with patch.object(discover.github,"gh",side_effect=[
            {"commit":{"sha":SHA}},
            {"total_count":50,"workflow_runs":history},
            {"commit":{"sha":SHA}},
        ]):
            observed=discover.last_main_audit({})
        self.assertEqual(observed["state"],"MAIN_AUDIT_HISTORY_INCOMPLETE")
        self.assertNotIn("workflow_run_id",observed)

    def test_no_current_sha_workflow_when_inventory_is_complete(self):
        with patch.object(discover.github,"gh",side_effect=[
            {"commit":{"sha":SHA}},
            {"total_count":1,"workflow_runs":[{
                "head_sha":"b"*40,"event":"push","id":400}]},
            {"commit":{"sha":SHA}},
        ]):
            observed=discover.last_main_audit({})
        self.assertEqual(observed["state"],"NO_CURRENT_MAIN_AUDIT")

    def test_no_pr_and_live_green_is_read_only(self):
        with patch.object(discover,"list_open",return_value=[]),patch.object(
            discover,"last_main_audit",return_value={"state":"MAIN_AUDIT_GREEN"}),patch.object(
            discover.publisher,"publish") as pub:
            result=discover.observe({})
            self.assertFalse(result["publish_allowed"])
            self.assertEqual(result["existing_pr_candidates"],[])
            pub.assert_not_called()

    def test_pr_listing_pagination_fails_closed(self):
        with patch.object(discover.github,"gh",return_value=[{}]*20):
            with self.assertRaisesRegex(ValueError,"paginated"):
                discover.list_open({})


if __name__=="__main__":
    unittest.main()
