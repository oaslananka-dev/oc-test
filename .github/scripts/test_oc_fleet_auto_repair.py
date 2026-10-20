"""Strict no-network tests for single-category autonomous Codacy repair selection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_auto_repair as selector
import oc_fleet_scoped_repair as repair

SHA="a"*40
PR=135
BASE_PR={
  "number":PR,"state":"open","draft":False,
  "user":{"login":"oaslananka"},
  "head":{"sha":SHA,"ref":"test/spi-test","repo":{"full_name":selector.REPO}},
  "base":{"ref":"main","repo":{"full_name":selector.REPO}},
}
PATCH="@@ -5,4 +5,5 @@\n context\n+        assert close_spy.call_count == 1\n context\n"
FILES=[{"filename":selector.FILE,"status":"modified","additions":1,
        "deletions":0,"patch":PATCH}]
CHECKS={"total_count":10,"check_runs":[
  {"id":88,"name":selector.CHECK,"head_sha":SHA,
   "status":"completed","conclusion":"action_required",
   "app":{"id":selector.APP_ID}},
  *[{"id":100+i,"name":name,"head_sha":SHA,
     "status":"completed","conclusion":"success","app":{"id":15368}}
    for i,name in enumerate(selector.REQUIRED) if name != selector.CHECK],
]}
ANNOTATIONS=[{"path":selector.FILE,"start_line":6,"end_line":6,
              "message":selector.MESSAGE}]


def captured(pr=None,files=None,checks=None,annotations=None):
    return [
        copy.deepcopy(FILES if files is None else files),
        copy.deepcopy(CHECKS if checks is None else checks),
        copy.deepcopy(ANNOTATIONS if annotations is None else annotations),
    ]


class AutoSelectTests(unittest.TestCase):
    def test_real_rule_exact_app_sha_annotation_added_line(self):
        with patch.object(selector.scoped.worker,"gh",side_effect=captured()):
            p=selector.select({},copy.deepcopy(BASE_PR))
        self.assertEqual(p["check_run_id"],88)
        self.assertEqual(p["check_conclusion"],"action_required")
        self.assertEqual(p["allowed_paths"],[selector.FILE])
        self.assertEqual(p["required_green_checks"],list(selector.REQUIRED))
        self.assertNotIn("assert close_spy",p["repair_goal"])
        with tempfile.TemporaryDirectory() as d:
            f=selector.store_policy(Path(d)/"private",p)
            self.assertEqual(repair.load_policy(f),p)
            self.assertEqual(f.stat().st_mode & 0o777,0o600)
            self.assertEqual(selector.store_policy(Path(d)/"private",p),f)
            with self.assertRaisesRegex(ValueError,"policy does not match"):
                selector.store_policy(Path(d)/"private",{**p,"head_sha":"b"*40})

    def test_newly_added_line_parser(self):
        self.assertTrue(selector.check_added_line(PATCH,6))
        self.assertFalse(selector.check_added_line(PATCH,5))
        self.assertFalse(selector.check_added_line(PATCH,7))
        self.assertFalse(selector.check_added_line(PATCH,0))
        self.assertFalse(selector.check_added_line(
            "@@ -10,2 +10,3 @@\n assert old == 1\n+    self.assertEqual(1,1)\n",11))

    def test_wrong_author_fork_draft_or_base_never_queries_worker(self):
        for update in (
            {"user":{"login":"someone"}}, {"draft":True},
            {"head":{"sha":SHA,"ref":"test/fork","repo":{"full_name":"elsewhere/x"}}},
            {"base":{"ref":"dev","repo":{"full_name":selector.REPO}}},
            {"state":"closed"},
        ):
            with self.subTest(update=update):
                with patch.object(selector.scoped.worker,"gh") as gh:
                    self.assertIsNone(selector.select({}, {**BASE_PR,**update}))
                    gh.assert_not_called()

    def test_no_extra_modified_file_or_cicd_path(self):
        for extra in (
            FILES+[{"filename":"AGENTS.md"}],
            [{**FILES[0],"filename":".github/workflows/ci.yml"}],
            [{**FILES[0],"additions":41}],
            [{**FILES[0],"status":"added"}],
        ):
            with self.subTest(extra=extra),patch.object(
                selector.scoped.worker,"gh",side_effect=captured(files=extra)):
                self.assertIsNone(selector.select({},BASE_PR))

    def test_other_checks_pending_failed_missing_or_wrong_app(self):
        cases=[]
        x=copy.deepcopy(CHECKS)
        x["check_runs"][1]["status"]="in_progress"
        cases.append(x)
        x=copy.deepcopy(CHECKS)
        x["check_runs"][1]["conclusion"]="failure"
        cases.append(x)
        x=copy.deepcopy(CHECKS)
        x["check_runs"][0]["app"]["id"]=999
        cases.append(x)
        x=copy.deepcopy(CHECKS)
        x["check_runs"][0]["conclusion"]="failure"
        cases.append(x)
        x=copy.deepcopy(CHECKS)
        x["check_runs"]=x["check_runs"][:-1];x["total_count"]-=1
        cases.append(x)
        x=copy.deepcopy(CHECKS);x["total_count"]+=1
        cases.append(x)
        for value in cases:
            with self.subTest(value=value),patch.object(
                selector.scoped.worker,"gh",side_effect=captured(checks=value)):
                self.assertIsNone(selector.select({},BASE_PR))

    def test_annotation_wrong_message_path_count_or_not_added(self):
        cases=[
          [{**ANNOTATIONS[0],"message":"execute shell command"}],
          [{**ANNOTATIONS[0],"path":"python/src/adxl355/transport.py"}],
          [{**ANNOTATIONS[0],"start_line":5,"end_line":5}],
          [],
          ANNOTATIONS*2,
        ]
        for value in cases:
            with self.subTest(value=value),patch.object(
                selector.scoped.worker,"gh",side_effect=captured(annotations=value)):
                self.assertIsNone(selector.select({},BASE_PR))

    def test_no_work_does_not_dispatch_or_create_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/"new"
            db=Path(d)/"db.sqlite"
            with patch.object(selector.scoped.worker,"identity",return_value={}),patch.object(
                selector,"list_open",return_value=[]),patch.object(
                selector.scoped,"execute") as run:
                self.assertEqual(selector.poll(folder,db,True),"none")
                run.assert_not_called()
            self.assertFalse(folder.exists())

    def test_automatic_select_only_one_and_no_write_in_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/"private"
            db=Path(d)/"db"
            with patch.object(selector.scoped.worker,"identity",return_value={}),patch.object(
                selector,"list_open",return_value=[BASE_PR,{**BASE_PR,"number":136}]),patch.object(
                selector,"select",return_value={"pr":PR,"head_sha":SHA}),patch.object(
                selector.scoped,"fetch_verified",return_value={}),patch.object(
                selector.scoped,"execute") as run:
                self.assertEqual(selector.poll(folder,db,False),"eligible")
                run.assert_not_called()
                self.assertFalse(folder.exists())

    def test_worker_capacity_rejects_more_than_one_existing_worker(self):
        account={}
        def active(status):
            return {"total_count":status,
                    "workflow_runs":[{"status":"in_progress"} for _ in range(status)]}
        with patch.object(selector.scoped.worker,"identity",return_value=account),patch.object(
            selector.scoped.worker,"gh",side_effect=[active(1),active(1),active(0),active(0)]):
            with self.assertRaisesRegex(ValueError,"concurrency"):
                selector.worker_capacity()
        with patch.object(selector.scoped.worker,"identity",return_value=account),patch.object(
            selector.scoped.worker,"gh",side_effect=[active(0),active(1),active(0),active(0)]):
            self.assertTrue(selector.worker_capacity())
        with patch.object(selector.scoped.worker,"identity",return_value=account),patch.object(
            selector.scoped.worker,"gh",return_value={
                "total_count":101,"workflow_runs":[{"status":"queued"}]}):
            with self.assertRaisesRegex(ValueError,"Incomplete"):
                selector.worker_capacity()

    def test_selected_candidate_live_uses_existing_publisher_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            db=base/"state.sqlite"
            folder=base/"private"
            policy={
                "repository":selector.REPO,"pr":PR,"head_sha":SHA,
                "head_branch":"test/spi-test","check_name":selector.CHECK,
                "check_run_id":88,"check_app_id":selector.APP_ID,
                "check_conclusion":"action_required",
                "required_green_checks":list(selector.REQUIRED),
                "allowed_paths":[selector.FILE],
                "repair_goal":("Replace the newly added bare assert with a "
                               "corresponding unittest assertion with the same behavior."),
            }
            with patch.object(selector.scoped.worker,"identity",return_value={}),patch.object(
                selector,"list_open",return_value=[BASE_PR]),patch.object(
                selector,"select",return_value=policy),patch.object(
                selector.scoped,"fetch_verified",return_value={}),patch.object(
                selector,"worker_capacity",return_value=True),patch.object(
                selector.scoped,"execute",return_value="single_worker_dispatched") as run:
                self.assertEqual(selector.poll(folder,db,True),"single_worker_dispatched")
                run.assert_called_once()
                passed=run.call_args.args
                self.assertEqual(repair.load_policy(passed[0]),policy)
                self.assertEqual(passed[1],db)
                self.assertIs(passed[2],True)

    def test_unreconciled_run_blocks_new_work(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"db"
            con=repair.database(db)
            job=repair.reserve(con,{**repair_test_policy(),"pr":PR})
            con.close()
            with patch.object(selector.scoped.worker,"identity",return_value={}),patch.object(
                selector,"list_open") as listings,patch.object(
                selector.scoped,"execute") as run:
                self.assertEqual(selector.poll(Path(d)/"policy",db,True),"blocked")
                listings.assert_not_called();run.assert_not_called()


def repair_test_policy():
    return {"pr":PR,"head_sha":SHA,"check_run_id":88}


if __name__=="__main__":
    unittest.main()
