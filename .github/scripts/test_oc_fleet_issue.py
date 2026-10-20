#!/usr/bin/env python3
"""Offline regression cases for models-free issue controller."""
import importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
spec=importlib.util.spec_from_file_location("controller",Path(__file__).with_name("oc_fleet_issue.py"))
controller=importlib.util.module_from_spec(spec)
sys.modules["oc_fleet_issue"]=controller
spec.loader.exec_module(controller)
class Args:
    model="opencode/big-pickle"
    profile="base"
    path=["playground/fleet/"]

class IssueControllerTests(unittest.TestCase):
    def test_default_worker_repo_preserved(self):
        repo={"full_name":controller.RUNNER,"private":False,"archived":False,
              "permissions":{"push":True}}
        workflow={"id":100,"state":"active",
                  "path":".github/workflows/oc-fleet-worker.yml"}
        with patch.object(controller,"gh",side_effect=[repo,workflow]) as get, \
             patch.object(controller,"cmd") as doppler:
            self.assertEqual(controller.authorized_worker_repo({},controller.RUNNER),
                             controller.RUNNER)
            self.assertEqual(get.call_count,2)
            doppler.assert_not_called()

    def test_cross_account_worker_must_be_allowlisted(self):
        with patch.object(controller,"cmd",
                          return_value=MagicMock(returncode=0,stdout="Other/worker")) as get, \
             patch.object(controller,"gh") as gh:
            with self.assertRaisesRegex(ValueError,"trusted allowlist"):
                controller.authorized_worker_repo({},"MakerParsDev/oc-test")
            gh.assert_not_called()
            self.assertEqual(get.call_count,1)

    def test_portable_worker_requires_write_access_and_active_workflow(self):
        repo={"full_name":"MakerParsDev/oc-test","private":False,"archived":False,
              "permissions":{"push":True}}
        workflow={"id":101,"state":"active",
                  "path":".github/workflows/oc-fleet-worker.yml"}
        with patch.object(controller,"cmd",
                          return_value=MagicMock(returncode=0,stdout="MakerParsDev/oc-test")):
            with patch.object(controller,"gh",side_effect=[repo,workflow]):
                self.assertEqual(controller.authorized_worker_repo({},"MakerParsDev/oc-test"),
                                 "MakerParsDev/oc-test")
            with patch.object(controller,"gh",side_effect=[{**repo,
                        "permissions":{"push":False}},workflow]):
                with self.assertRaisesRegex(ValueError,"cannot dispatch"):
                    controller.authorized_worker_repo({},"MakerParsDev/oc-test")
            with patch.object(controller,"gh",side_effect=[repo,{**workflow,
                        "state":"disabled_manually"}]):
                with self.assertRaisesRegex(ValueError,"no active expected workflow"):
                    controller.authorized_worker_repo({},"MakerParsDev/oc-test")

    def test_receipt_cannot_switch_worker_without_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"receipt.json"
            f.write_text(json.dumps({"worker_repo":"MakerParsDev/oc-test",
                                     "run_id":123,"history":[]}),encoding="utf-8")
            with patch.object(controller,"gh") as gh:
                with self.assertRaisesRegex(ValueError,"different worker repository"):
                    controller.retry_receipt({},f,True,runner=controller.RUNNER)
                gh.assert_not_called()

    def test_codacy_windows_launcher(self):
        with patch.object(controller.shutil, "which",
                          return_value=r"C:\Tools\codacy-analysis.cmd"):
            self.assertEqual(
                controller.resolve_cli(["codacy-analysis", "--version"], windows=True),
                [r"C:\Tools\codacy-analysis.cmd", "--version"])
        self.assertEqual(
            controller.resolve_cli(["codacy-analysis", "--version"], windows=False),
            ["codacy-analysis", "--version"])

    def test_bounded_python_codacy_gate(self):
        with patch.object(controller, "cmd", return_value=MagicMock(returncode=0)) as call:
            controller.quality_gates(Path("."), ["tests/test_hashing.py"],
                                     ["codacy"], "oaslananka/nerlex")
            self.assertEqual(call.call_count, 2)
            invocation = call.call_args_list[-1]
            self.assertIn("Ruff", invocation.args[0])
            self.assertIn("--tool-timeout", invocation.args[0])
            self.assertEqual(invocation.kwargs["deadline"], 150)

    def test_subprocess_timeout_fails_closed(self):
        with patch.object(controller.subprocess, "run",
                          side_effect=controller.subprocess.TimeoutExpired(
                              cmd=["codacy-analysis"], timeout=3)):
            with self.assertRaisesRegex(ValueError, "time limit"):
                controller.cmd(["codacy-analysis"], {}, deadline=3)

    def test_codacy_generated_files_never_enter_publisher_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            repo=root/"target"
            repo.mkdir()
            env=dict(os.environ)
            def git(*args):
                return controller.cmd(["git","-C",str(repo),*args],env).stdout.strip()
            git("init","-q")
            git("config","user.name","Fleet")
            git("config","user.email","fleet@example.invalid")
            git("config","core.autocrlf","false")
            target=repo/"tests"/"test_hashing.py"
            target.parent.mkdir()
            target.write_bytes(b"before\n")
            git("add",".")
            git("commit","-qm","base")
            sha=git("rev-parse","HEAD")
            target.write_bytes(b"after\n")
            patch_file=root/"changes.patch"
            patch_file.write_bytes(controller.subprocess.check_output(
                ["git","-C",str(repo),"diff","--binary","HEAD"]))
            def generate_extra(scan, paths, integrations, project):
                self.assertNotEqual(Path(scan),repo)
                unwanted=Path(scan)/".codacy"/".gitignore"
                unwanted.parent.mkdir()
                unwanted.write_text("generated/\n",encoding="utf-8")
            with patch.object(controller,"quality_gates",side_effect=generate_extra):
                controller.isolated_quality_gates(
                    repo,sha,patch_file,["tests/test_hashing.py"],["codacy"],
                    "oaslananka/nerlex",env,root)
            self.assertFalse((repo/".codacy").exists())
            paths, _ = controller.scoped_patch(repo,["tests/test_hashing.py"])
            self.assertEqual(paths,["tests/test_hashing.py"])

    def test_public_pr_metadata_omits_execution_details(self):
        args=Args()
        manifest=controller.manifest_for(
            "oaslananka/nerlex",26,"a"*40,"main",
            "Resolve GitHub issue #26 in oaslananka/nerlex.\n"
            "Title: test: canonical JSON hashing edge cases (temporary OpenCode fleet pilot)\n"
            "Create regression tests.",args,["oaslananka/nerlex"])
        title,body,branch=controller.public_change_metadata(
            manifest,["tests/test_hashing.py"])
        self.assertEqual(title,"test: canonical JSON hashing edge cases")
        self.assertIn("Closes #26",body)
        self.assertIn("tests/test_hashing.py",body)
        self.assertTrue(branch.startswith("change/26-"))
        for public_text in (title,body,branch):
            self.assertNotRegex(public_text.lower(),
                r"oc-test|opencode|multica|worker|agent|fleet|pilot|github.com/.*/actions/runs")

    def test_public_pr_falls_back_when_issue_is_internal(self):
        args=Args()
        m=controller.manifest_for("oaslananka/nerlex",50,"a"*40,"main",
            "Title: OpenCode worker pilot for code change\n\nPlease update tests.",
            args,["oaslananka/nerlex"])
        title,body,branch=controller.public_change_metadata(
            m,["tests/test_hashing.py"])
        self.assertEqual(title,"test: improve test hashing coverage")
        self.assertIn("Closes #50",body)
        self.assertNotIn("worker",title.lower())

    def test_manifest_ok(self):
        m=controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,
             "main","Fix the sample issue safely.",Args(),["oaslananka-dev/oc-test"])
        self.assertEqual(m["issue_key"],"GH-12")
        self.assertEqual(m["source"],"rules")
        self.assertFalse(m["publish"])
        self.assertTrue(m["task_id"].startswith("gh-12-"))
    def test_operator_opt_in_multica_tracking_not_from_issue_text(self):
        args=Args()
        args.multica_issue="OAS-11"
        m=controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,"main",
             "Issue body says OAS-99 should be used instead.",args,
             ["oaslananka-dev/oc-test"])
        self.assertEqual(m["multica_issue"],"OAS-11")
        args.multica_issue="oas-11"
        with self.assertRaisesRegex(ValueError,"Multica issue"):
            controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,"main",
                 "Fix a regression safely.",args,["oaslananka-dev/oc-test"])

    def test_manifest_reject_unsafe(self):
        a=Args()
        for paths in [[],[".github/"],["playground/../"],["playground/fleet/","playground/FLEET/"]]:
            with self.subTest(paths=paths),self.assertRaises(ValueError):
                b=Args();b.path=paths
                controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,"main",
                                        "Fix the issue and run tests.",b,["oaslananka-dev/oc-test"])
        with self.assertRaises(ValueError):
            controller.manifest_for("other-owner/repo",12,"a"*40,"main",
                 "Fix the issue and run tests.",a,["oaslananka-dev/oc-test"])
    def test_issue_validation(self):
        data={"full_name":"oaslananka-dev/oc-test","permissions":{"push":True},
             "default_branch":"main","private":False}
        issue={"number":12,"state":"open","title":"Task","body":"Fix a broken fixture."}
        def fake_gh(env,path):
            return issue if path.endswith("/issues/12") else data
        def fake_cmd(cmd,env,**kw):
            return MagicMock(stdout="a"*40,returncode=0)
        with patch.object(controller,"gh",side_effect=fake_gh),patch.object(controller,"cmd",side_effect=fake_cmd):
            self.assertEqual(controller.repo_issue("oaslananka-dev/oc-test",12,{})[1],"a"*40)
            data["permissions"]["push"]=False
            with self.assertRaisesRegex(ValueError,"cannot push"):
                controller.repo_issue("oaslananka-dev/oc-test",12,{})
    def test_repo_squash_policy_fails_closed(self):
        data={"full_name":"oaslananka-dev/oc-test","permissions":{"push":True},
              "default_branch":"main","private":False,"allow_squash_merge":True,
              "squash_merge_commit_title":"COMMIT_OR_PR_TITLE",
              "squash_merge_commit_message":"COMMIT_MESSAGES"}
        with patch.object(controller,"gh",return_value=data):
            with self.assertRaisesRegex(ValueError,"squash defaults"):
                controller.repo_issue("oaslananka-dev/oc-test",12,{})
        data["squash_merge_commit_title"]="PR_TITLE"
        data["squash_merge_commit_message"]="BLANK"
        def fake_gh(env,path):
            if path.endswith("/issues/12"):
                return {"number":12,"state":"open","title":"Task","body":"Cover edge cases."}
            return data
        with patch.object(controller,"gh",side_effect=fake_gh),patch.object(
                controller,"cmd",return_value=MagicMock(stdout="a"*40)):
            self.assertEqual(controller.repo_issue(
                "oaslananka-dev/oc-test",12,{})[1],"a"*40)

    def test_explicit_external_pr_only_preserves_repo_squash_defaults(self):
        data={"full_name":"oaslananka/adxl355","permissions":{"push":True},
              "default_branch":"main","private":False,"allow_squash_merge":True,
              "squash_merge_commit_title":"COMMIT_OR_PR_TITLE",
              "squash_merge_commit_message":"COMMIT_MESSAGES"}
        def mock_gh(env,path):
            if path.endswith("/issues/124"):
                return {"number":124,"state":"open","title":"Close a leaked SPI handle",
                        "body":"Fix and test the cleanup failure."}
            return data
        with patch.object(controller,"gh",side_effect=mock_gh),patch.object(
            controller,"cmd",return_value=MagicMock(stdout="a"*40)):
            with self.assertRaisesRegex(ValueError,"squash defaults"):
                controller.repo_issue("oaslananka/adxl355",124,{})
            self.assertEqual(controller.repo_issue(
                "oaslananka/adxl355",124,{},external_pr_only=True)[1],"a"*40)
            data["squash_merge_commit_title"]="PR_TITLE"
            data["squash_merge_commit_message"]="BLANK"
            self.assertEqual(controller.repo_issue(
                "oaslananka/adxl355",124,{},external_pr_only=True)[1],"a"*40)
            data["squash_merge_commit_message"]="PR_BODY"
            with self.assertRaisesRegex(ValueError,"approved target"):
                controller.repo_issue("oaslananka/adxl355",124,{},external_pr_only=True)
        # The approval is never implicitly inherited by the main pilot repo.
        data.update(full_name=controller.RUNNER,
                    squash_merge_commit_message="COMMIT_MESSAGES")
        with patch.object(controller,"gh",return_value=data):
            with self.assertRaisesRegex(ValueError,"approved target"):
                controller.repo_issue(controller.RUNNER,124,{},external_pr_only=True)

    def test_private_target_fails_closed(self):
        with patch.object(controller,"gh",return_value={"full_name":"oaslananka-dev/oc-test",
             "permissions":{"push":True},"private":True}):
            with self.assertRaisesRegex(ValueError,"private target"):
                controller.repo_issue("oaslananka-dev/oc-test",12,{})
    def test_multica_rejects_unapproved_repo_without_secret_mutation(self):
        with patch.object(controller,"cmd",return_value=MagicMock(
                stdout="oaslananka-dev/oc-test,oaslananka/adxl355",
                returncode=0)) as call:
            self.assertIn("oaslananka/adxl355",
                          controller.require_approved_target("oaslananka/adxl355"))
            with self.assertRaisesRegex(ValueError,"not explicitly approved"):
                controller.require_approved_target("evil/other")
            self.assertEqual(call.call_count,2)
            self.assertTrue(all("get" in c.args[0] for c in call.call_args_list))

    def test_allowlist_registered(self):
        with patch.object(controller,"cmd",return_value=MagicMock(stdout="oaslananka-dev/oc-test",returncode=0)) as call:
            allow=controller.allow_target({},"org/name",preview=False)
            self.assertIn("org/name",allow)
            self.assertEqual(call.call_count,2)
        with patch.object(controller,"cmd",return_value=MagicMock(stdout="oaslananka-dev/oc-test",returncode=0)) as call:
            controller.allow_target({},"org/name",preview=True)
            self.assertEqual(call.call_count,1)
    def test_dispatch_binds_native_run(self):
        m=controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,"main",
                 "Fix a test regression safely.",Args(),["oaslananka-dev/oc-test"])
        with patch.object(controller,"api_post",return_value={"workflow_run_id":1234}) as p:
            self.assertEqual(controller.dispatch({},m),1234)
            self.assertEqual(p.call_args.args[2]["return_run_details"],True)
        with patch.object(controller,"api_post",return_value={}):
            with self.assertRaisesRegex(ValueError,"run ID missing"):
                controller.dispatch({},m)
    def test_receipts_unique_by_repo_issue(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{"LOCALAPPDATA":tmp}):
            a=controller.receipt("alice/repo",42)
            self.assertEqual(a,controller.receipt("ALICE/REPO",42))
            self.assertNotEqual(a,controller.receipt("alice/repo",43))
    def test_retry_receipt_fail_closed_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"receipt.json"
            p.write_text(json.dumps({"run_id":71,"manifest":{
                "repository":"oaslananka-dev/oc-test","task_id":"gh-32-first"},
                "history":[]}),encoding="utf-8")
            def prior(env,path):
                return {"id":71,"status":"completed","conclusion":"failure"}
            with patch.object(controller,"gh",side_effect=prior):
                with self.assertRaisesRegex(ValueError,"already dispatched"):
                    controller.retry_receipt({},p,False)
                hist=controller.retry_receipt({},p,True)
                self.assertEqual(hist[0]["run_id"],71)
                self.assertEqual(hist[0]["outcome"],"failure")
            with patch.object(controller,"gh",return_value={
                "id":71,"status":"in_progress","conclusion":None}):
                with self.assertRaises(ValueError):
                    controller.retry_receipt({},p,True)
            p.write_text(json.dumps({"run_id":71,"manifest":{
                "repository":"oaslananka-dev/oc-test","task_id":"gh-32-third"},
                "history":[{"run_id":5},{"run_id":6}]}),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"maximum three"):
                controller.retry_receipt({},p,True)

    def test_integrations_are_selected_not_issue_controlled(self):
        m=controller.manifest_for("oaslananka-dev/oc-test", 42, "a"*40,
          "main", "Test a simple change and run all checks.", Args(),
          ["oaslananka-dev/oc-test"])
        self.assertEqual(m["integrations"],[])
        class QualityArgs(Args):
            integration=["codacy","sonarqube"]
        selected=controller.manifest_for("oaslananka-dev/oc-test",42,"a"*40,
          "main", "Test a simple change and run all checks.",QualityArgs(),
          ["oaslananka-dev/oc-test"])
        self.assertEqual(selected["integrations"],["codacy","sonarqube"])

    def test_completed_worker_jobs_allow_skipped_telemetry_probe(self):
        legacy={"total_count":2,"jobs":[
            {"name":"validate","conclusion":"success"},
            {"name":"execute","conclusion":"success"}]}
        controller.verify_worker_jobs(legacy)
        current={"total_count":3,"jobs":legacy["jobs"]+[
            {"name":"telemetry-probe","conclusion":"skipped"}]}
        controller.verify_worker_jobs(current)
        for invalid in (
            {**current,"total_count":2},
            {**current,"jobs":current["jobs"][:2]+[
                {"name":"telemetry-probe","conclusion":"success"}]},
            {**current,"jobs":current["jobs"][:2]+[
                {"name":"unexpected","conclusion":"skipped"}]},
            {**current,"jobs":[current["jobs"][0]]*3},
            {**current,"jobs":current["jobs"][:2]+[
                {"name":"telemetry-probe","conclusion":"failure"}]},
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    ValueError,"worker job"):
                controller.verify_worker_jobs(invalid)

    def test_source_verification_blocks_fake_run(self):
        m=controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,"main",
                 "Fix the test issue safely.",Args(),["oaslananka-dev/oc-test"])
        def fake_gh(env,path):
            if path.endswith("runs/777"):
                return {"id":777,"workflow_id":50,"repository":{"id":2,"full_name":controller.RUNNER},
                    "status":"completed","conclusion":"failure","event":"workflow_dispatch",
                    "head_branch":"main","run_attempt":1}
            if path.endswith(controller.WORKFLOW):
                return {"id":50}
            return {"id":2}
        with patch.object(controller,"gh",side_effect=fake_gh):
            with self.assertRaisesRegex(ValueError,"untrusted worker run"):
                controller.verify_artifact({},777,m)
    def test_base_advance_blocks_publish(self):
        m=controller.manifest_for("oaslananka-dev/oc-test",12,"a"*40,"main",
             "Fix this issue and run tests.",Args(),["oaslananka-dev/oc-test"])
        with patch.object(controller,"cmd",return_value=MagicMock(stdout="b"*40,returncode=0)):
            with self.assertRaisesRegex(ValueError,"target base changed"):
                controller.publish({},"oaslananka-dev/oc-test","main",m,{},b"",readonly=True)



import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class SafeBaseAdvanceTests(unittest.TestCase):
    def git(self, repo, *parts, check=True):
        return subprocess.run(
            ["git", "-C", str(repo), *parts],
            text=True, capture_output=True, check=check,
        ).stdout.strip()

    def fixture(self, directory):
        repo = Path(directory)
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Test")
        self.git(repo, "config", "user.email", "test@example.invalid")
        (repo / "src").mkdir()
        (repo / "src" / "thing.py").write_text("original\n")
        (repo / "notes.md").write_text("v1\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "base")
        return self.git(repo, "rev-parse", "HEAD")

    def commit(self, repo, name, body):
        p = Path(repo) / name
        p.write_text(body)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "next")
        return self.git(repo, "rev-parse", "HEAD")

    def test_unrelated_monotonic_advance_allows_same_scope_objects(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.fixture(folder)
            b = self.commit(folder, "notes.md", "v2\n")
            self.assertEqual(
                controller.safe_base_advance(folder, a, b, ["src/thing.py"],
                                             ["src/thing.py"], os.environ.copy()),
                ["notes.md"])

    def test_changed_file_blocks_and_directory_scope_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.fixture(folder)
            b = self.commit(folder, "src/thing.py", "changed\n")
            for scope in ("src/thing.py", "src/"):
                with self.subTest(scope=scope), self.assertRaisesRegex(
                        ValueError, "changed approved scope"):
                    controller.safe_base_advance(folder, a, b,
                                                 ["src/thing.py"], [scope],
                                                 os.environ.copy())

    def test_diverged_or_rewritten_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.fixture(folder)
            self.git(folder, "switch", "-q", "-c", "unrelated")
            b = self.commit(folder, "notes.md", "new\n")
            self.git(folder, "switch", "-q", "-c", "other", a)
            c = self.commit(folder, "notes.md", "other\n")
            with self.assertRaisesRegex(ValueError, "diverged"):
                controller.safe_base_advance(folder, b, c,
                                             ["src/thing.py"], ["src/thing.py"],
                                             os.environ.copy())

    def test_missing_approved_file_in_both_bases_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.fixture(folder)
            b = self.commit(folder, "notes.md", "v2\n")
            result = controller.safe_base_advance(
                folder, a, b, ["src/new.py"], ["src/new.py"], os.environ.copy())
            self.assertEqual(result, ["notes.md"])

if __name__=="__main__":unittest.main(verbosity=2)
