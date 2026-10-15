#!/usr/bin/env python3
"""Offline negative/positive checks for the separate fleet PR publisher."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from oc_fleet_contract import scoped_patch

spec=importlib.util.spec_from_file_location("publisher", Path(__file__).with_name("oc_fleet_publish.py"))
pub=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)

SHA="a"*40
RUN_ID=987654
TASK_ID="oas-7-pilot-001"

def run_data():
    return {"id":RUN_ID,"workflow_id":123,"event":"workflow_dispatch",
            "head_branch":"main","status":"completed","conclusion":"success",
            "head_sha":SHA,"run_attempt":1,
            "repository":{"id":pub.REPO_ID,"full_name":pub.REPO}}

def evidence(patch):
    return {"schema_version":1,"task_id":TASK_ID,"source":"rules",
            "repository":pub.REPO,"checkout_sha":SHA,"issue_key":"OAS-7",
            "run_id":str(RUN_ID),"run_attempt":"1","outcome":"SUCCEEDED",
            "paths":["playground/fleet/check.txt"],
            "patch_sha256":hashlib.sha256(patch).hexdigest(),"published":False}

class PublisherTests(unittest.TestCase):
    def test_successful_provenance(self):
        self.assertEqual(pub.verify_source_run(run_data(),123,RUN_ID,SHA),1)
        pub.verify_jobs([{"name":"validate","conclusion":"success"},
                         {"name":"execute","conclusion":"success"}])
        self.assertEqual(pub.verify_artifact_metadata([{
            "name":f"fleet-{RUN_ID}-1-{TASK_ID}","expired":False,
            "workflow_run":{"id":RUN_ID}}],RUN_ID,1,TASK_ID),
            f"fleet-{RUN_ID}-1-{TASK_ID}")
        patch=b"diff --git a/playground/fleet/check.txt b/playground/fleet/check.txt\n"
        self.assertEqual(pub.verify_evidence(evidence(patch),patch,RUN_ID,1,SHA,TASK_ID),
                         ["playground/fleet/check.txt"])

    def test_source_run_rejection(self):
        for bad in [
            {"conclusion":"failure"},{"status":"in_progress"},
            {"head_sha":"b"*40},{"head_branch":"feature"},
            {"event":"pull_request"},{"id":RUN_ID+1},{"workflow_id":9},
            {"run_attempt":False},{"repository":{"id":999,"full_name":pub.REPO}},
        ]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                pub.verify_source_run({**run_data(),**bad},123,RUN_ID,SHA)

    def test_source_jobs_rejection(self):
        for jobs in [[],[{"name":"validate","conclusion":"success"}],
                     [{"name":"validate","conclusion":"success"},{"name":"execute","conclusion":"skipped"}],
                     [{"name":"validate","conclusion":"success"},{"name":"execute","conclusion":"failure"}],
                     [{"name":"validate","conclusion":"success"},{"name":"execute","conclusion":"success"},{"name":"other","conclusion":"success"}]]:
            with self.subTest(jobs=jobs),self.assertRaises(ValueError):
                pub.verify_jobs(jobs)

    def test_artifact_rejection(self):
        valid={"name":f"fleet-{RUN_ID}-1-{TASK_ID}","expired":False,
               "workflow_run":{"id":RUN_ID}}
        for items in [[],[valid,valid],[{**valid,"expired":True}],
                      [{**valid,"workflow_run":{"id":RUN_ID+1}}],
                      [{**valid,"name":"wrong"}]]:
            with self.subTest(items=items),self.assertRaises(ValueError):
                pub.verify_artifact_metadata(items,RUN_ID,1,TASK_ID)

    def test_evidence_rejection(self):
        patch=b"diff --git a/playground/fleet/check.txt b/playground/fleet/check.txt\n"
        cases=[
            {"published":True},{"outcome":"FAILED"},{"checkout_sha":"b"*40},
            {"run_id":"3"},{"run_attempt":"2"},{"repository":"other/repo"},
            {"paths":[".github/workflows/ci.yml"]},
            {"paths":["playground/fleet/../../.github/secrets.yml"]},
            {"paths":["playground/fleet/a.txt","playground/fleet/a.txt"]},
            {"issue_key":"invalid"},{"source":"unknown"},
            {"patch_sha256":"b"*64},{"unknown":"extra"},
        ]
        for bad in cases:
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                pub.verify_evidence({**evidence(patch),**bad},patch,RUN_ID,1,SHA,TASK_ID)
        with self.assertRaises(ValueError):
            pub.verify_evidence(evidence(patch),b"",RUN_ID,1,SHA,TASK_ID)
        with self.assertRaises(ValueError):
            pub.verify_evidence(evidence(patch),patch+b"changed",RUN_ID,1,SHA,TASK_ID)

    def test_patch_application_exact_scopes(self):
        with tempfile.TemporaryDirectory() as t:
            repo=Path(t)
            def git(*args):
                return subprocess.check_output(["git","-C",str(repo),*args],text=True).strip()
            git("init","-q");git("config","user.name","Fleet test")
            git("config","user.email","fleet@example.invalid")
            git("config","core.autocrlf","false")
            base=repo/"baseline.txt"
            base.write_text("base\n",encoding="utf-8")
            git("add",".");git("commit","-qm","base")
            sha=git("rev-parse","HEAD")
            target=repo/"playground/fleet/check.txt"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"OAS-7 test\n")
            paths,patch=scoped_patch(repo,pub.TRUSTED_SCOPES)
            self.assertEqual(paths,["playground/fleet/check.txt"])
            with tempfile.TemporaryDirectory() as artifact_dir:
                artifact=Path(artifact_dir)/"changes.patch"
                artifact.write_bytes(patch)
                git("reset","--hard","HEAD")
                if target.exists(): target.unlink()
                self.assertEqual(
                    pub.verify_local_patch(repo,artifact,paths,sha,hashlib.sha256(patch).hexdigest()),
                    paths)
                with self.assertRaises(ValueError):
                    pub.verify_local_patch(repo,artifact,paths,"b"*40,hashlib.sha256(patch).hexdigest())

if __name__=="__main__":
    unittest.main(verbosity=2)
