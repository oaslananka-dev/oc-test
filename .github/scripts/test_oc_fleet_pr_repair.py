"""Mocked offline assertions for SHA-bound F1 repair publication."""
import hashlib
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import oc_fleet_pr_repair as repair

SHA = "a" * 40
AFTER = "b" * 40
ISSUE = {
    "repository": repair.REPO, "pr_number": 2, "head_sha": SHA,
    "state": "REPAIR_REQUIRED", "dispatch_allowed": True,
    "repair_key": "c" * 20, "failing_checks": ["fixture-quality"],
    "pending_checks": [], "missing_checks": [],
}


def pr(sha=SHA, branch=repair.BRANCH):
    return {"number": 2, "state": "open", "merged": False, "draft": False,
            "head": {"sha": sha, "ref": branch, "repo": {"full_name": repair.REPO}},
            "base": {"ref": "main", "repo": {"full_name": repair.REPO}}}


class RepairTests(unittest.TestCase):
    def test_live_scope_checks_identity_branch_and_sha(self):
        with patch.object(repair.controller, "gh", return_value=pr()):
            self.assertEqual(repair.approved_pr({}), SHA)
        for bad in ({"number": 3}, {"state": "closed"}, {"draft": True},
                    {"head": {"sha": SHA, "ref": "unrelated",
                              "repo": {"full_name": repair.REPO}}},
                    {"head": {"sha": SHA, "ref": repair.BRANCH,
                              "repo": {"full_name": "other/repo"}}},
                    {"head": {"sha": "nope", "ref": repair.BRANCH,
                              "repo": {"full_name": repair.REPO}}}):
            with self.subTest(bad=bad),patch.object(repair.controller,"gh",return_value={**pr(),**bad}):
                with self.assertRaises(ValueError):
                    repair.approved_pr({})

    def test_repair_only_after_trusted_fixture_failure(self):
        self.assertTrue(repair.can_repair(ISSUE))
        for delta in (
            {"state": "WAITING_CHECKS"},
            {"state": "BOT_FEEDBACK_TRIAGE"},
            {"dispatch_allowed": False},
            {"pending_checks": ["semgrep-cloud-platform/scan"]},
            {"missing_checks": ["baseline"]},
            {"failing_checks": ["fixture-quality","status:codacy"]},
            {"failing_checks": ["baseline"]},
            {"pr_number": 1},
        ):
            with self.subTest(delta=delta):
                self.assertFalse(repair.can_repair({**ISSUE,**delta}))

    def test_manifest_strict_path_and_nonpublishing_worker(self):
        m=repair.build_manifest(SHA,[repair.REPO])
        self.assertEqual(m["checkout_sha"],SHA)
        self.assertEqual(m["allowed_paths"],[repair.FILE])
        self.assertFalse(m["publish"])
        self.assertEqual(m["phase"],"implement")
        self.assertIn("exactly these three lines",m["prompt"])

    def test_bad_or_stale_patch_cannot_enter_publisher(self):
        with patch.object(repair,"approved_pr",return_value=AFTER),patch.object(
            repair.controller,"cmd") as cmd:
            with self.assertRaisesRegex(ValueError,"head moved"):
                repair.publish_repair({},SHA,{},b"diff")
            cmd.assert_not_called()
        with patch.object(repair,"approved_pr",return_value=SHA),patch.object(
            repair.controller,"cmd") as cmd:
            with self.assertRaisesRegex(ValueError,"artifact"):
                repair.publish_repair({},SHA,{"checkout_sha":SHA,"repository":repair.REPO,
                                             "paths":[".github/workflows/ci.yml"]},b"patch")
            cmd.assert_not_called()

    def test_publisher_fast_forwards_exact_existing_branch_without_new_pr(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            remote, seed = base / "remote.git", base / "seed"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
            for key, value in (("user.name", "Fixture Owner"),
                               ("user.email", "fixture@example.invalid")):
                subprocess.run(["git", "-C", str(seed), "config", key, value], check=True)
            file = seed / repair.FILE
            file.parent.mkdir(parents=True)
            file.write_text("ocfilo F1 worker proof\nBig Pickle ran on Ubuntu\nBAD_LINE\n")
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-qm", "initial"], check=True)
            sha=subprocess.check_output(["git","-C",str(seed),"rev-parse","HEAD"],text=True).strip()
            subprocess.run(["git","-C",str(seed),"branch",repair.BRANCH],check=True)
            subprocess.run(["git","-C",str(seed),"remote","add","origin",str(remote)],check=True)
            subprocess.run(["git","-C",str(seed),"push","-q","origin","main",repair.BRANCH],check=True)
            file.write_text("ocfilo F1 worker proof\nBig Pickle ran on Ubuntu\n"
                            "ocfilo F1 CI PASS\n")
            patchdata=subprocess.check_output(["git","-C",str(seed),"diff","--binary","HEAD"])
            file.write_text("ocfilo F1 worker proof\nBig Pickle ran on Ubuntu\nBAD_LINE\n")
            evidence={"checkout_sha":sha,"repository":repair.REPO,
                      "paths":[repair.FILE],
                      "patch_sha256":hashlib.sha256(patchdata).hexdigest()}
            original=repair.controller.cmd
            def local_cmd(argv,env,cwd=None,**kwargs):
                if argv[:3]==["gh","repo","clone"]:
                    target=argv[4]
                    argv=["git","clone","-q",str(remote),target]
                return original(argv,env,cwd=cwd,**kwargs)
            def actual_pr(_):
                head=subprocess.check_output(
                    ["git","--git-dir",str(remote),"rev-parse",
                     "refs/heads/"+repair.BRANCH],text=True).strip()
                return head
            user={"login":"oaslananka","id":12345,"name":"Fixture Owner","type":"User"}
            with patch.object(repair,"approved_pr",side_effect=actual_pr),\
                 patch.object(repair.controller,"cmd",side_effect=local_cmd),\
                 patch.object(repair.controller,"gh",return_value=user):
                result=repair.publish_repair({},sha,evidence,patchdata)
            self.assertNotEqual(result,sha)
            self.assertEqual(actual_pr({}),result)
            after=subprocess.check_output(["git","--git-dir",str(remote),
                "show","refs/heads/"+repair.BRANCH+":"+repair.FILE],text=True)
            self.assertEqual(after.splitlines()[-1],"ocfilo F1 CI PASS")
            self.assertEqual(subprocess.check_output(
                ["git","--git-dir",str(remote),"branch","--list","--format=%(refname:short)"],
                text=True).strip().splitlines(),["main",repair.BRANCH])

    def test_existing_reservation_disallows_blind_second_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            con=repair.queue.connect(Path(folder)/"state.db")
            try:
                first=repair.queue.reserve(con,ISSUE,SHA,"f1-pr2-one")["job"]
                self.assertEqual(first["state"],"RESERVED")
                second=repair.queue.reserve(con,ISSUE,SHA,"f1-pr2-two")
                self.assertEqual(second["outcome"],"ALREADY_ACTIVE")
            finally:
                con.close()

    def test_dry_run_only_plans_no_model_run_or_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            fake={"GH_TOKEN":"mock"}
            with patch.object(repair.controller,"identity",side_effect=[fake,fake]),\
                 patch.object(repair,"live_decision",return_value=(SHA,ISSUE)),\
                 patch.object(repair.controller,"authorized_worker_repo",return_value=repair.WORKER),\
                 patch.object(repair.controller,"cmd",return_value=MagicMock(returncode=0,stdout=repair.REPO)),\
                 patch.object(repair.controller,"dispatch") as dispatch:
                self.assertEqual(repair.execute_once(Path(folder)/"queue.db",True),"planned")
                dispatch.assert_not_called()
                self.assertFalse((Path(folder)/"queue.db").exists())

    def test_failing_ci_does_not_authorize_dispatch_without_fixture_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(repair.controller,"identity",return_value={"GH_TOKEN":"mock"}),\
                 patch.object(repair,"live_decision",return_value=(SHA,{**ISSUE,"state":"NEEDS_HUMAN"})),\
                 patch.object(repair.controller,"dispatch") as dispatch:
                self.assertEqual(repair.execute_once(Path(folder)/"queue.db",False),"no_work")
                dispatch.assert_not_called()


if __name__=="__main__":
    unittest.main()
