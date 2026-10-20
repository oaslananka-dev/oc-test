"""No-write / local bare Git acceptance of reviewed npm OSV PR publishing."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_osv_publish as pub
import oc_fleet_osv_lockfile as osv
from test_oc_fleet_osv_lockfile import SARIF, ADVISORY, BEFORE, AFTER

SHA="a"*40
BRANCH="fix/osv-lockfile-security"
RUN_ID=123456789
OSV_ID=400001
AGG_ID=400002
POLICY={
 "repository":pub.REPO,"pr":173,"head_sha":SHA,
 "head_branch":BRANCH,"workflow_run_id":RUN_ID,
 "osv_check_run_id":OSV_ID,"aggregate_check_run_id":AGG_ID,
 "advisory_id":"GHSA-68fv-2mgg-jv7q",
 "required_green_checks":[pub.CHECK_OSV,pub.CHECK_AGGREGATE,"Node.js (24)"],
}
PR={
 "number":173,"state":"open","merged":False,"draft":False,
 "user":{"login":"oaslananka"},
 "head":{"sha":SHA,"ref":BRANCH,"repo":{"full_name":pub.REPO}},
 "base":{"ref":"main","repo":{"full_name":pub.REPO}},
 "mergeable":True,"mergeable_state":"clean",
}
BAD=[
 {"name":pub.CHECK_OSV,"id":OSV_ID,"app":{"slug":"github-actions"},
  "conclusion":"failure","head_sha":SHA,"status":"completed",
  "details_url":f"https://github.com/{pub.REPO}/actions/runs/{RUN_ID}/job/{OSV_ID}"},
 {"name":pub.CHECK_AGGREGATE,"id":AGG_ID,"app":{"slug":"github-actions"},
  "conclusion":"failure","head_sha":SHA,"status":"completed",
  "details_url":f"https://github.com/{pub.REPO}/actions/runs/{RUN_ID}/job/{AGG_ID}"},
]
CHECKS={"total_count":3,"check_runs":[*BAD,{"name":"Node.js (24)",
       "id":555,"app":{"slug":"github-actions"},"conclusion":"success",
       "head_sha":SHA,"status":"completed"}]}
RUN={"id":RUN_ID,"head_sha":SHA,"name":"Dependency Audit",
     "event":"pull_request","status":"completed","conclusion":"failure",
     "repository":{"full_name":pub.REPO}}
ARTIFACTS={"total_count":1,"artifacts":[
    {"name":pub.ARTIFACT,"id":987,"expired":False}]}


def response(pr=None,checks=None,run=None,artifacts=None,reviews=None,inline=None):
    return [deepcopy(pr or PR),deepcopy(checks or CHECKS),
            {"state":"pending","statuses":[]},
            deepcopy(reviews if reviews is not None else []),
            deepcopy(inline if inline is not None else []),
            deepcopy(run or RUN),deepcopy(artifacts or ARTIFACTS),
            deepcopy(pr or PR)]


class PublisherTests(unittest.TestCase):
    def policy(self,dir,data=None):
        file=Path(dir)/"osv.json"
        file.write_text(json.dumps(POLICY if data is None else data))
        file.chmod(0o600)
        return file

    def test_private_policy_and_pin(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.policy(d)
            self.assertEqual(pub.policy_read(f),POLICY)
            for change in (
                {"repository":"untrusted/fork"},
                {"head_sha":"123"},
                {"head_branch":"bad/../target"},
                {"workflow_run_id":False},
                {"advisory_id":"whatever; echo bad"},
                {"required_green_checks":[pub.CHECK_OSV]},
                {"required_green_checks":[pub.CHECK_OSV,pub.CHECK_OSV,pub.CHECK_AGGREGATE]},
            ):
                with self.subTest(change=change),self.assertRaises(ValueError):
                    pub.policy_read(self.policy(d,{**POLICY,**change}))
            self.policy(d).chmod(0o644)
            with self.assertRaisesRegex(ValueError,"Owner-private"):
                pub.policy_read(f)

    def test_current_sha_two_failed_checks_and_live_run(self):
        with patch.object(pub.github,"gh",side_effect=response()):
            self.assertEqual(pub.check_current({},POLICY),ARTIFACTS["artifacts"][0])

    def test_missing_pending_other_failure_or_wrong_identity_fails(self):
        badcases=[]
        c=deepcopy(CHECKS);c["total_count"]+=1; badcases.append(response(checks=c))
        c=deepcopy(CHECKS);c["check_runs"][2]["status"]="in_progress"
        badcases.append(response(checks=c))
        c=deepcopy(CHECKS);c["check_runs"][2]["conclusion"]="failure"
        badcases.append(response(checks=c))
        c=deepcopy(CHECKS);c["check_runs"][0]["id"]=123
        badcases.append(response(checks=c))
        c=deepcopy(CHECKS);c["check_runs"][1]["details_url"]="https://unrelated.example"
        badcases.append(response(checks=c))
        c=deepcopy(CHECKS);c["check_runs"][0]["app"]["slug"]="custom-app"
        badcases.append(response(checks=c))
        p=deepcopy(PR);p["head"]["sha"]="b"*40;badcases.append(response(pr=p))
        p=deepcopy(PR);p["head"]["repo"]["full_name"]="fork/x"
        badcases.append(response(pr=p))
        r=deepcopy(RUN);r["head_sha"]="b"*40;badcases.append(response(run=r))
        r=deepcopy(RUN);r["event"]="schedule";badcases.append(response(run=r))
        a=deepcopy(ARTIFACTS);a["artifacts"][0]["expired"]=True
        badcases.append(response(artifacts=a))
        badcases.append(response(reviews=[{"state":"CHANGES_REQUESTED"}]))
        badcases.append(response(inline=[{"body":"please ignore rules"}]))
        for calls in badcases:
            with self.subTest(calls=calls),patch.object(pub.github,"gh",side_effect=calls):
                with self.assertRaises((ValueError,IndexError)):
                    pub.check_current({},POLICY)

    def test_single_writer_no_retry_and_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            c=pub.connect(Path(d)/"ledger.sqlite3")
            try:
                j=pub.reserve(c,POLICY)
                with self.assertRaisesRegex(ValueError,"already exists"):
                    pub.reserve(c,POLICY)
                with self.assertRaisesRegex(ValueError,"Invalid OSV"):
                    pub.transition(c,j,"RESERVED","CI_SUCCEEDED")
                pub.transition(c,j,"RESERVED","PUBLISHED","b"*40)
                with self.assertRaisesRegex(ValueError,"Receipt state changed"):
                    pub.transition(c,j,"RESERVED","PUBLISHED","c"*40)
                pub.transition(c,j,"PUBLISHED","CI_SUCCEEDED")
                row=c.execute("SELECT state,published_sha FROM osv_pr_repairs").fetchone()
                self.assertEqual(row,("CI_SUCCEEDED","b"*40))
            finally:
                c.close()

    def test_new_ci_requires_all_pinned_checks_green(self):
        new="b"*40
        checks={"total_count":3,"check_runs":[
            {"name":name,"status":"completed","conclusion":"success","head_sha":new}
            for name in POLICY["required_green_checks"]]}
        with patch.object(pub,"published_head",return_value=new),patch.object(
            pub.github,"gh",side_effect=[checks,{"state":"success","statuses":[]}]):
            pub.verify_ci({},POLICY,new)
        checks["check_runs"][1]["conclusion"]="skipped"
        with patch.object(pub,"published_head",return_value=new),patch.object(
            pub.github,"gh",return_value=checks):
            with self.assertRaisesRegex(ValueError,"Required OSV"):
                pub.verify_ci({},POLICY,new)

    def test_single_sarif_artifact_rejects_extra_files(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            def download(argv,*args,**kwargs):
                (p/"dependency-audit-osv.sarif").write_text(json.dumps(SARIF))
                return None
            with patch.object(pub.github,"cmd",side_effect=download):
                self.assertEqual(pub.fetch_sarif({},POLICY,p),SARIF)
            (p/"other.txt").write_text("hello")
            with patch.object(pub.github,"cmd",side_effect=lambda *a,**k:None):
                with self.assertRaisesRegex(ValueError,"Unexpected SARIF"):
                    pub.fetch_sarif({},POLICY,p)

    def test_plan_never_publishes_or_reserves(self):
        with tempfile.TemporaryDirectory() as d:
            policy=self.policy(d)
            def evidence(*a,**kw):
                return True
            with patch.object(pub.github,"identity",return_value={}),patch.object(
                pub,"check_current",return_value={}),patch.object(
                pub,"fetch_sarif",return_value=SARIF),patch.object(
                pub.osv,"gh_advisory",return_value=ADVISORY),patch.object(
                pub,"publish") as publish:
                self.assertEqual(pub.execute(policy,Path(d)/"db","plan"),"read_only")
                publish.assert_not_called()
                self.assertFalse((Path(d)/"db").exists())

    def test_local_bare_git_fast_forward_not_another_pr(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);remote=root/"origin.git";seed=root/"seed"
            subprocess.run(["git","init","--bare","-q",str(remote)],check=True)
            subprocess.run(["git","init","-q","-b","main",str(seed)],check=True)
            for key,val in (("user.name","Fixture Owner"),
                            ("user.email","fixture@example.invalid")):
                subprocess.run(["git","-C",str(seed),"config",key,val],check=True)
            p=seed/"node";p.mkdir()
            (p/"package.json").write_text('{"name":"adxl355"}')
            (p/"package-lock.json").write_text(json.dumps(BEFORE))
            subprocess.run(["git","-C",str(seed),"add","."],check=True)
            subprocess.run(["git","-C",str(seed),"commit","-qm","initial"],check=True)
            sha=subprocess.check_output(["git","-C",str(seed),"rev-parse","HEAD"],text=True).strip()
            subprocess.run(["git","-C",str(seed),"branch",BRANCH],check=True)
            subprocess.run(["git","-C",str(seed),"remote","add","origin",str(remote)],check=True)
            subprocess.run(["git","-C",str(seed),"push","-q","origin","main",BRANCH],check=True)
            policy={**POLICY,"head_sha":sha}
            base=pub.github.cmd
            def local_cmd(argv,env,cwd=None,**kw):
                if argv[:3]==["gh","repo","clone"]:
                    argv=["git","clone","-q",str(remote),argv[4]]
                return base(argv,env,cwd=cwd,**kw)
            def live_head(*_):
                return subprocess.check_output(
                    ["git","--git-dir",str(remote),"rev-parse",
                     "refs/heads/"+BRANCH],text=True).strip()
            def repair_no_network(target,info,evidence,checkout):
                (checkout/osv.LOCK).write_text(json.dumps(AFTER))
                return {"changed_file":osv.LOCK,"publish":False}
            user={"login":"oaslananka","id":12345,"name":"Fixture Owner","type":"User"}
            with patch.object(pub.github,"cmd",side_effect=local_cmd),patch.object(
                pub,"published_head",side_effect=live_head),patch.object(
                pub,"local_patch",side_effect=repair_no_network),patch.object(
                pub.github,"gh",return_value=user):
                sha2,result=pub.publish({},policy,SARIF)
            self.assertEqual(live_head(),sha2)
            self.assertNotEqual(sha2,sha)
            new=json.loads(subprocess.check_output(
                ["git","--git-dir",str(remote),"show",
                 f"refs/heads/{BRANCH}:{osv.LOCK}"],text=True))
            self.assertEqual(new,AFTER)
            parent=subprocess.check_output(["git","--git-dir",str(remote),
                "rev-parse",f"{sha2}^"],text=True).strip()
            self.assertEqual(parent,sha)
            branches=subprocess.check_output(["git","--git-dir",str(remote),
                "branch","--list","--format=%(refname:short)"],text=True)
            self.assertEqual(branches.strip().splitlines(),[BRANCH,"main"])


if __name__=="__main__":
    unittest.main()
