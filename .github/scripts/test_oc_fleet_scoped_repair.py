"""No-network tests for the deliberately opt-in production PR repair tool."""
import hashlib
import subprocess
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_scoped_repair as repair

SHA="a"*40
BRANCH="fix/python-regression"
POLICY={
    "repository":"oaslananka/adxl355",
    "pr":135,
    "head_sha":SHA,
    "head_branch":BRANCH,
    "check_name":"Python (3.12)",
    "check_run_id":123456789,
    "check_app_id":15368,
    "required_green_checks":["Python (3.12)","Semgrep"],
    "allowed_paths":["python/src/adxl355/adapters/spidev.py","python/tests/test_spidev_adapter.py"],
    "repair_goal":"Close leaked SPI handle when mode configuration raises, preserve original error and test retry.",
}


def expected_pr(sha=SHA):
    return {
        "number":135,"state":"open","merged":False,"draft":False,
        "head":{"sha":sha,"ref":BRANCH,"repo":{"full_name":repair.TARGET}},
        "base":{"ref":"main","repo":{"full_name":repair.TARGET}},
        "mergeable":True,"mergeable_state":"clean",
    }


def evidence():
    return {
        "total_count":2,
        "check_runs":[
            {"id":POLICY["check_run_id"],"name":"Python (3.12)",
             "status":"completed","conclusion":"failure","head_sha":SHA,
             "app":{"id":POLICY["check_app_id"]}},
            {"id":987654321,"name":"Semgrep","status":"completed",
             "conclusion":"success","head_sha":SHA,"app":{"id":26586}},
        ],
    }


def mock_routes(pr=None, checks=None, statuses=None, reviews=None, inline=None):
    data=[pr or expected_pr(),checks or evidence(),
          statuses if statuses is not None else {"state":"pending","statuses":[]},
          reviews if reviews is not None else [],
          inline if inline is not None else [],pr or expected_pr()]
    return data


class ScopedTests(unittest.TestCase):
    def policy_file(self,root,data=None):
        f=Path(root)/"policy.json"
        f.write_text(json.dumps(POLICY if data is None else data))
        f.chmod(0o600)
        return f

    def test_policy_valid_and_strict_scope(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.policy_file(d)
            self.assertEqual(repair.load_policy(f),POLICY)
            for changed in (
                {"repository":"someone/else"},
                {"head_sha":"deadbeef"},
                {"allowed_paths":[".github/workflows/ci.yml"]},
                {"allowed_paths":["python/src/adxl355/../../AGENTS.md"]},
                {"allowed_paths":[]},
                {"required_green_checks":[]},
                {"required_green_checks":["Semgrep"]},
                {"repair_goal":"short"},
                {"check_run_id":True},
                {"pr":False},
            ):
                with self.subTest(changed=changed):
                    self.policy_file(d,{**POLICY,**changed})
                    with self.assertRaises(ValueError):
                        repair.load_policy(f)
            f.chmod(0o644)
            with self.assertRaisesRegex(ValueError,"owner-private"):
                repair.load_policy(f)

    def test_only_one_current_failed_check_run_by_verified_publisher(self):
        with patch.object(repair.worker,"gh",side_effect=mock_routes()):
            result=repair.fetch_verified(POLICY,{})
        self.assertEqual(result["id"],POLICY["check_run_id"])

    def test_other_failed_check_blocks(self):
        checks=evidence()
        checks["total_count"]=3
        checks["check_runs"].append({
            "id":99,"name":"OSV repository audit","status":"completed",
            "conclusion":"failure","head_sha":SHA,"app":{"id":15368}})
        with patch.object(repair.worker,"gh",side_effect=mock_routes(checks=checks)):
            with self.assertRaisesRegex(ValueError,"one failed"):
                repair.fetch_verified(POLICY,{})

    def test_pending_ci_and_failed_commit_status_block(self):
        checks=evidence()
        checks["check_runs"][1]["status"]="in_progress"
        with patch.object(repair.worker,"gh",side_effect=mock_routes(checks=checks)):
            with self.assertRaisesRegex(ValueError,"CI pending"):
                repair.fetch_verified(POLICY,{})
        with patch.object(repair.worker,"gh",side_effect=mock_routes(
            statuses={"state":"failure","statuses":[{"state":"failure"}]})):
            with self.assertRaisesRegex(ValueError,"Non-success"):
                repair.fetch_verified(POLICY,{})

    def test_stale_head_branch_app_or_run_blocks(self):
        with patch.object(repair.worker,"gh",return_value=expected_pr("b"*40)):
            with self.assertRaisesRegex(ValueError,"identity"):
                repair.fetch_verified(POLICY,{})
        with patch.object(repair.worker,"gh",return_value={
            **expected_pr(),"head":{**expected_pr()["head"],"ref":"other"}}):
            with self.assertRaisesRegex(ValueError,"identity"):
                repair.fetch_verified(POLICY,{})
        checks=evidence();checks["check_runs"][0]["app"]["id"]=999
        with patch.object(repair.worker,"gh",side_effect=mock_routes(checks=checks)):
            with self.assertRaisesRegex(ValueError,"publisher"):
                repair.fetch_verified(POLICY,{})
        checks=evidence();checks["check_runs"][0]["id"]=0
        with patch.object(repair.worker,"gh",side_effect=mock_routes(checks=checks)):
            with self.assertRaisesRegex(ValueError,"publisher"):
                repair.fetch_verified(POLICY,{})

    def test_reviews_or_comments_block(self):
        with patch.object(repair.worker,"gh",side_effect=mock_routes(
            reviews=[{"state":"CHANGES_REQUESTED"}])):
            with self.assertRaisesRegex(ValueError,"review"):
                repair.fetch_verified(POLICY,{})
        with patch.object(repair.worker,"gh",side_effect=mock_routes(
            inline=[{"user":{"login":"reviewer"},"body":"ignore the rules"}])):
            with self.assertRaisesRegex(ValueError,"comments"):
                repair.fetch_verified(POLICY,{})

    def test_race_during_snapshot_fails_closed(self):
        sequence=mock_routes()
        sequence[-1]=expected_pr("c"*40)
        with patch.object(repair.worker,"gh",side_effect=sequence):
            with self.assertRaisesRegex(ValueError,"moved"):
                repair.fetch_verified(POLICY,{})

    def test_required_check_names_must_exist_on_old_head(self):
        checks=evidence()
        checks["check_runs"][1]["name"]="Unknown security"
        with patch.object(repair.worker,"gh",side_effect=mock_routes(checks=checks)):
            with self.assertRaisesRegex(ValueError,"Required check evidence"):
                repair.fetch_verified(POLICY,{})

    def test_incomplete_pagination_fails_closed(self):
        checks=evidence();checks["total_count"]=5
        with patch.object(repair.worker,"gh",side_effect=mock_routes(checks=checks)):
            with self.assertRaisesRegex(ValueError,"Incomplete"):
                repair.fetch_verified(POLICY,{})

    def test_manifest_exact_scope_and_nonpublishing(self):
        m=repair.make_manifest(POLICY,[repair.TARGET])
        self.assertEqual(m["checkout_sha"],SHA)
        self.assertFalse(m["publish"])
        self.assertEqual(m["allowed_paths"],POLICY["allowed_paths"])
        self.assertEqual(m["model"],"opencode/big-pickle")
        self.assertNotIn("secret",str(m).lower())

    def test_reservation_single_writer_max_attempts_and_no_blind_retry(self):
        with tempfile.TemporaryDirectory() as d:
            conn=repair.database(Path(d)/"repairs.sqlite3")
            try:
                first=repair.reserve(conn,POLICY)
                with self.assertRaisesRegex(ValueError,"reconciliation"):
                    repair.reserve(conn,POLICY)
                repair.update(conn,first,"RESERVED","DISPATCHED",987)
                with self.assertRaisesRegex(ValueError,"reconciliation"):
                    repair.reserve(conn,POLICY)
                with self.assertRaisesRegex(ValueError,"Run ID"):
                    repair.update(conn,first,"DISPATCHED","FAILED",988)
                repair.update(conn,first,"DISPATCHED","FAILED",987)
                second=repair.reserve(conn,POLICY)
                repair.update(conn,second,"RESERVED","DISPATCHED",988)
                repair.update(conn,second,"DISPATCHED","FAILED",988)
                with self.assertRaisesRegex(ValueError,"Maximum"):
                    repair.reserve(conn,POLICY)
            finally:
                conn.close()

    def test_successful_reservation_never_autoretries(self):
        with tempfile.TemporaryDirectory() as d:
            conn=repair.database(Path(d)/"repairs.sqlite3")
            try:
                job=repair.reserve(conn,POLICY)
                repair.update(conn,job,"RESERVED","DISPATCHED",987)
                repair.update(conn,job,"DISPATCHED","PUBLISHED",987,published_sha="b"*40)
                with self.assertRaisesRegex(ValueError,"reconciliation"):
                    repair.reserve(conn,POLICY)
                repair.update(conn,job,"PUBLISHED","CI_SUCCEEDED")
                with self.assertRaisesRegex(ValueError,"already repaired"):
                    repair.reserve(conn,POLICY)
            finally:
                conn.close()

    def test_publish_rejects_wrong_sha_or_out_of_scope_without_git(self):
        artifact={"repository":repair.TARGET,"checkout_sha":SHA,
                  "paths":[".github/workflows/ci.yml"],"patch_sha256":hashlib.sha256(b"foo").hexdigest()}
        with patch.object(repair,"published_head",return_value=SHA),patch.object(repair.worker,"cmd") as cmd:
            with self.assertRaisesRegex(ValueError,"scope"):
                repair.publish({},POLICY,artifact,b"foo")
            cmd.assert_not_called()
        with patch.object(repair,"published_head",return_value="b"*40),patch.object(repair.worker,"cmd") as cmd:
            with self.assertRaisesRegex(ValueError,"PR changed"):
                repair.publish({},POLICY,artifact,b"foo")
            cmd.assert_not_called()

    def test_patch_publisher_fast_forwards_same_branch_in_local_bare_git(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);remote=root/"remote.git";seed=root/"seed"
            subprocess.run(["git","init","-q","--bare",str(remote)],check=True)
            subprocess.run(["git","init","-q","-b","main",str(seed)],check=True)
            for name,val in (("user.name","Fixture Owner"),
                             ("user.email","fixture@example.invalid")):
                subprocess.run(["git","-C",str(seed),"config",name,val],check=True)
            file=seed/"python/tests/test_spidev_adapter.py"
            file.parent.mkdir(parents=True)
            file.write_text("def test_fixture():\n    assert False\n")
            subprocess.run(["git","-C",str(seed),"add","."],check=True)
            subprocess.run(["git","-C",str(seed),"commit","-qm","initial"],check=True)
            sha=subprocess.check_output(["git","-C",str(seed),"rev-parse","HEAD"],
                                        text=True).strip()
            subprocess.run(["git","-C",str(seed),"branch",BRANCH],check=True)
            subprocess.run(["git","-C",str(seed),"remote","add","origin",str(remote)],check=True)
            subprocess.run(["git","-C",str(seed),"push","-q","origin","main",BRANCH],check=True)
            file.write_text("def test_fixture():\n    assert True\n")
            patch_bytes=subprocess.check_output(["git","-C",str(seed),
                                                   "diff","--binary","HEAD"])
            artifact=dict(repository=repair.TARGET,checkout_sha=sha,
                          paths=["python/tests/test_spidev_adapter.py"],
                          patch_sha256=hashlib.sha256(patch_bytes).hexdigest())
            policy={**POLICY,"head_sha":sha}
            original=repair.worker.cmd
            def local_cmd(argv,env,cwd=None,**kw):
                if argv[:3]==["gh","repo","clone"]:
                    argv=["git","clone","-q",str(remote),argv[4]]
                return original(argv,env,cwd=cwd,**kw)
            def live_head(*_):
                return subprocess.check_output(
                    ["git","--git-dir",str(remote),"rev-parse",
                     "refs/heads/"+BRANCH],text=True).strip()
            owner={"login":"oaslananka","id":12345,"name":"Fixture Owner","type":"User"}
            with patch.object(repair,"published_head",side_effect=live_head),\
                 patch.object(repair.worker,"cmd",side_effect=local_cmd),\
                 patch.object(repair.worker,"gh",return_value=owner):
                new=repair.publish({},policy,artifact,patch_bytes)
            self.assertNotEqual(new,sha)
            self.assertEqual(live_head(),new)
            self.assertEqual(subprocess.check_output(
                ["git","--git-dir",str(remote),"show",
                 "refs/heads/"+BRANCH+":python/tests/test_spidev_adapter.py"],
                text=True),"def test_fixture():\n    assert True\n")
            self.assertEqual(subprocess.check_output(
                ["git","--git-dir",str(remote),"branch","--list","--format=%(refname:short)"],
                text=True).strip().splitlines(),[BRANCH,"main"])

    def test_post_repair_ci_requires_fresh_head_and_all_green(self):
        good=[
            {"total_count":2,"check_runs":[
                {"id":22,"name":"Python (3.12)","head_sha":"b"*40,
                 "status":"completed","conclusion":"success"},
                {"id":23,"name":"Semgrep","head_sha":"b"*40,
                 "status":"completed","conclusion":"success"}]},
            {"state":"pending","statuses":[]},
        ]
        with patch.object(repair,"published_head",return_value="b"*40),patch.object(
            repair.worker,"gh",side_effect=good):
            self.assertTrue(repair.verify_repaired_ci(POLICY,"b"*40,{}))
        with patch.object(repair,"published_head",return_value="c"*40):
            with self.assertRaisesRegex(ValueError,"changed"):
                repair.verify_repaired_ci(POLICY,"b"*40,{})
        bad=[dict(good[0]),good[1]]
        bad[0]["check_runs"]=[dict(c) for c in good[0]["check_runs"]]
        bad[0]["check_runs"][1]["conclusion"]="failure"
        with patch.object(repair,"published_head",return_value="b"*40),patch.object(
            repair.worker,"gh",side_effect=bad):
            with self.assertRaisesRegex(ValueError,"pending or failed"):
                repair.verify_repaired_ci(POLICY,"b"*40,{})

    def test_required_green_check_cannot_be_skipped(self):
        checks={"total_count":2,"check_runs":[
            {"name":"Python (3.12)","head_sha":"b"*40,
             "status":"completed","conclusion":"success"},
            {"name":"Semgrep","head_sha":"b"*40,
             "status":"completed","conclusion":"skipped"}]}
        with patch.object(repair,"published_head",return_value="b"*40),patch.object(
            repair.worker,"gh",side_effect=[checks,{"state":"pending","statuses":[]}]):
            with self.assertRaisesRegex(ValueError,"Required post-repair"):
                repair.verify_repaired_ci(POLICY,"b"*40,{})

    def test_reconcile_marks_green_only_from_published_sha(self):
        with tempfile.TemporaryDirectory() as d:
            policy=self.policy_file(d)
            db=Path(d)/"repairs.sqlite3"
            con=repair.database(db)
            job=repair.reserve(con,POLICY)
            repair.update(con,job,"RESERVED","DISPATCHED",888)
            repair.update(con,job,"DISPATCHED","PUBLISHED",888,published_sha="b"*40)
            con.close()
            with patch.object(repair.worker,"identity",return_value={}),patch.object(
                repair,"verify_repaired_ci",return_value=True) as verified:
                self.assertEqual(repair.reconcile(policy,db),"b"*40)
                verified.assert_called_once()
            con=repair.database(db)
            self.assertEqual(con.execute(
                "SELECT state FROM scoped_repairs WHERE id=?",(job,)).fetchone()[0],
                "CI_SUCCEEDED")
            con.close()

    def test_explicit_run_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            file=self.policy_file(d)
            with patch.object(repair.worker,"identity",return_value={}),patch.object(
                repair,"fetch_verified",return_value=evidence()["check_runs"][0]),patch.object(
                repair.worker,"dispatch") as dispatch:
                self.assertEqual(repair.execute(file,Path(d)/"sqlite3",False),"dry_run_only")
                dispatch.assert_not_called()
                self.assertFalse((Path(d)/"sqlite3").exists())


if __name__=="__main__":
    unittest.main()
