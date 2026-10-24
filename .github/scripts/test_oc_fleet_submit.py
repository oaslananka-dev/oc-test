"""Negative-first offline tests of one-command Fleet intake and progress view."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_submit as f

REPO = "oaslananka/adxl355"
ISSUE = 55
PATHS = ["python/src/", "python/tests/test_spidev_adapter.py"]
KEY = "OAS-777"
META = {
    "fleet_target_repo": REPO,
    "fleet_target_issue": str(ISSUE),
    "fleet_target_account": "oaslananka",
    "fleet_worker_repo": f.WORKER_REPO,
    "fleet_worker_account": f.WORKER_ACCOUNT,
    "fleet_allowed_path": PATHS[0],
    "fleet_allowed_paths": json.dumps(PATHS[1:], separators=(",", ":")),
    "fleet_model": "opencode/big-pickle",
    "fleet_profile": "base",
    "fleet_agent": "build",
    "fleet_auto_merge": False,
    "fleet_external_pr_only": True,
    "fleet_ready": False,
}


def options():
    return argparse.Namespace(repo=REPO, issue=ISSUE, path=list(PATHS),
                              model="opencode/big-pickle", profile="base",
                              agent="build",
                              confirm_target=f"{REPO}#{ISSUE}")


class SubmitTests(unittest.TestCase):
    def test_local_path_validation_and_non_pilot_only(self):
        for paths in (
            [], ["src/", "src/a.py"], ["src/", "src/"],
            [".github/workflows/ci.yml"], ["../secret"], ["src/../bad.py"],
            ["src/.env"], ["src/node_modules/"], [r"src\evil.py"],
            ["src/file.py"] * 2, ["ok.py"]*13,
        ):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                f.validate_inputs(REPO, ISSUE, paths, "opencode/big-pickle",
                                  "base", "build")
        for repo in ("foo", "oaslananka-dev/oc-test", "some/repo/other"):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                f.validate_inputs(repo, ISSUE, PATHS, "opencode/big-pickle",
                                  "base", "build")
        for model, profile, agent in (
            ("paid/model", "base", "build"),
            ("opencode/big-pickle", "unsafe", "build"),
            ("opencode/big-pickle", "base", "superadmin"),
        ):
            with self.subTest(model=model, profile=profile, agent=agent), self.assertRaises(ValueError):
                f.validate_inputs(REPO, ISSUE, PATHS, model, profile, agent)
        f.validate_inputs(REPO, ISSUE, PATHS, "opencode/big-pickle",
                          "base", "build")

    def test_preflight_is_existing_worker_dry_run_only(self):
        evidence = {"status": "DRY_RUN", "repo": REPO, "issue": ISSUE,
                    "scope": PATHS, "sha": "a"*40}
        with patch.object(f, "run", return_value=json.dumps(evidence)) as runner:
            got = f.preview(REPO, ISSUE, PATHS, "opencode/big-pickle",
                            "base", "build")
            self.assertEqual(got["state"], "ready_to_submit")
            self.assertFalse(got["auto_merge"])
            argv = runner.call_args.args[0]
            self.assertIn("--require-approved-target", argv)
            self.assertIn("--external-pr-only", argv)
            self.assertIn("--dry-run", argv)
            self.assertNotIn("--dispatch-only", argv)
            self.assertNotIn("--retry-failed", argv)
            self.assertEqual(argv.count("--path"), 2)
        for bad in ({**evidence, "status": "WORKER_DISPATCHED"},
                    {**evidence, "scope": ["src/"]},
                    {**evidence, "repo": "other/repo"}):
            with self.subTest(data=bad),patch.object(f,"run",return_value=json.dumps(bad)):
                with self.assertRaises(ValueError):
                    f.preview(REPO, ISSUE, PATHS, "opencode/big-pickle",
                              "base", "build")

    def test_preview_failure_cannot_create_task(self):
        with patch.object(f, "run", side_effect=ValueError("not allowed")),              patch.object(f, "multica") as db:
            with self.assertRaisesRegex(ValueError, "not allowed"):
                f.preview(REPO, ISSUE, PATHS, "opencode/big-pickle",
                          "base", "build")
            db.assert_not_called()

    def test_list_pagination_complete_and_bounded(self):
        def data(*args, **kwargs):
            offset = int(args[args.index("--offset")+1])
            if offset == 0:
                return json.dumps({"issues": [{"identifier": "OAS-1"}]*100,
                                   "has_more": True})
            return json.dumps({"issues": [{"identifier":"OAS-2"}],
                               "has_more": False})
        with patch.object(f, "multica", side_effect=data) as read:
            self.assertEqual(len(f.list_raw()), 101)
            self.assertEqual(read.call_count, 2)
        with patch.object(f, "multica", return_value=json.dumps({
                "issues":[{"identifier":"OAS-1"}],"has_more": True})):
            with self.assertRaisesRegex(ValueError, "pagination"):
                f.list_raw()

    def test_duplicate_target_is_never_reenqueued(self):
        items = [{"identifier":"OAS-31","metadata":{
            "fleet_target_repo":REPO.upper(),"fleet_target_issue":str(ISSUE)}}]
        self.assertEqual(f.duplicates(items,REPO,ISSUE),["OAS-31"])
        self.assertFalse(f.duplicates(items,"oaslananka/fovux-kit",ISSUE))

    def test_submit_rejects_missing_exact_target_confirmation_before_io(self):
        with patch.object(f, "preview") as preview:
            with self.assertRaisesRegex(ValueError,"confirmation"):
                f.main(["submit","--repo",REPO,"--issue",str(ISSUE),
                        "--path","src/","--confirm-target",REPO+"#99"])
            preview.assert_not_called()

    def test_safe_staging_and_final_ready_commit(self):
        with tempfile.TemporaryDirectory() as td:
            self.issue = {"identifier":KEY, "status":"todo", "project_id":f.PROJECT,
                          "metadata":{},"title":"Task"}
            writes=[]
            def update(key, field, value, kind="string"):
                self.assertEqual(key, KEY)
                writes.append((field,value,kind))
                self.issue["metadata"][field] = (
                    value=="true" if kind=="bool" else value
                )
            def read(key):
                self.assertEqual(key,KEY)
                return copy.deepcopy(self.issue)
            with patch.object(f,"LOCK",Path(td)/"lock"),                  patch.object(f,"preview",return_value={"base_sha":"a"*40}),                  patch.object(f,"list_raw",return_value=[]),                  patch.object(f,"multica",return_value=json.dumps({"identifier":KEY})) as create,                  patch.object(f,"update",side_effect=update),                  patch.object(f.contract,"read_issue",side_effect=read),                  patch.object(f.contract,"validate_issue",return_value=None) as validation:
                res=f.submit(options())
                self.assertEqual(res["state"],"queued")
                self.assertEqual(res["ready"],True)
                self.assertEqual(create.call_count,1)
                self.assertEqual(create.call_args.args[0],"create")
                self.assertEqual(writes[-1],("fleet_ready","true","bool"))
                self.assertEqual(writes[-2],("fleet_ready","false","bool"))
                self.assertEqual(self.issue["metadata"]["fleet_auto_merge"],False)
                self.assertEqual(self.issue["metadata"]["fleet_external_pr_only"],True)
                self.assertEqual(self.issue["metadata"]["fleet_allowed_paths"],
                                 json.dumps(PATHS[1:],separators=(",",":")))
                validation.assert_called_once()
                self.assertFalse(validation.call_args.args[-1])

    def test_failure_before_final_ready_never_admits_worker(self):
        with tempfile.TemporaryDirectory() as td:
            issue={"identifier":KEY,"status":"todo","project_id":f.PROJECT,
                   "metadata":{}}
            writes=[]
            def update(key,field,value,kind="string"):
                writes.append((field,value))
                if field=="fleet_profile":
                    raise RuntimeError("metadata failure")
                issue["metadata"][field]=value
            with patch.object(f,"LOCK",Path(td)/"lock"),                  patch.object(f,"preview",return_value={"base_sha":"a"*40}),                  patch.object(f,"list_raw",return_value=[]),                  patch.object(f,"multica",return_value=json.dumps({"identifier":KEY})),                  patch.object(f,"update",side_effect=update):
                with self.assertRaisesRegex(ValueError,"partially staged"):
                    f.submit(options())
            self.assertNotIn(("fleet_ready","true"),writes)

    def test_duplicate_or_locked_submission_never_creates_issue(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(f,"LOCK",Path(td)/"lock"),                  patch.object(f,"preview",return_value={"base_sha":"a"*40}),                  patch.object(f,"list_raw",return_value=[{
                     "identifier":"OAS-31","metadata":{
                         "fleet_target_repo":REPO,
                         "fleet_target_issue":str(ISSUE)}}]),                  patch.object(f,"multica") as create:
                with self.assertRaisesRegex(ValueError,"Existing Fleet task"):
                    f.submit(options())
                create.assert_not_called()

    def test_read_only_status_and_list_cannot_mutate(self):
        fixture={"identifier":KEY,"project_id":f.PROJECT,"status":"in_review",
                 "title":"Task","metadata":{**META,"fleet_pull_request":"321",
                          "fleet_run_id":"111","fleet_result":"pr_created"}}
        with patch.object(f.contract,"read_issue",return_value=fixture),              patch.object(f,"update") as mutation:
            self.assertEqual(f.main(["status",KEY])["pull_request"],"321")
            mutation.assert_not_called()
        with patch.object(f,"list_raw",return_value=[fixture]),              patch.object(f,"update") as mutation:
            self.assertEqual(f.main(["list"])["count"],1)
            mutation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
