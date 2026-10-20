"""Offline fail-closed tests of decoupled Multica dispatch / result publication."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import io
import contextlib
import sys
import oc_fleet_issue as controller

import oc_fleet_multica_detached as queue

PID="0506e0e5-01a7-4de9-b63b-67e993809721"
KEY="OAS-19"
META={
    "fleet_target_repo":"oaslananka-dev/oc-test",
    "fleet_target_issue":"81",
    "fleet_worker_repo":"MakerParsDev/oc-test",
    "fleet_worker_account":"MakerParsDev",
    "fleet_target_account":"oaslananka",
    "fleet_allowed_path":"playground/fleet/scale4-alpha.json",
    "fleet_auto_merge":True,
    "fleet_ready":True,
}
PENDING={**META,"fleet_ready":False,"fleet_run_id":"12345",
         "fleet_result":"worker_dispatched"}
ISSUE={"identifier":KEY,"project_id":PID,"status":"todo",
       "assignee_type":None,"metadata":META}
DOC={"run_id":12345,"worker_repo":META["fleet_worker_repo"],
     "manifest":{
         "repository":META["fleet_target_repo"],"issue_key":"GH-81",
         "allowed_paths":[META["fleet_allowed_path"]],
         "multica_issue":KEY}}
RUN={"id":12345,"repository":{"full_name":"MakerParsDev/oc-test"},
     "status":"in_progress","event":"workflow_dispatch",
     "head_branch":"main","run_attempt":1}


class DetachedTests(unittest.TestCase):
    def test_dispatch_starts_one_run_without_waiting_and_persists_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            receipt=root/"controller.json"
            def command(argv,timeout=30):
                if "--dispatch-only" in argv:
                    receipt.write_text(json.dumps(DOC))
                    return 'DISPATCHED: worker\n'+json.dumps({
                        "status":"WORKER_DISPATCHED","run_id":"12345",
                        "worker_repo":"MakerParsDev/oc-test",
                        "target_repo":"oaslananka-dev/oc-test",
                        "target_issue":"81","checkout_sha":"a"*40})+"\n"
                return ""
            with patch.object(queue.handoff,"read_issue",return_value=ISSUE),patch.object(
                queue.poller,"controller_receipt",return_value=receipt),patch.object(
                queue,"execute",side_effect=command) as e:
                self.assertEqual(queue.dispatch_one(ISSUE,PID,True,root),"dispatched")
                self.assertTrue((root/(KEY+".claim")).exists())
                self.assertEqual((root/(KEY+".claim")).stat().st_mode & 0o777,0o600)
                cmds=[c.args[0] for c in e.call_args_list]
                self.assertEqual(sum("--dispatch-only" in c for c in cmds),1)
                self.assertEqual(sum("resume" in c for c in cmds),0)
                self.assertEqual(sum("fleet_ready" in c for c in cmds),1)
                self.assertIn("in_progress",cmds[-1])

    def test_encoded_extra_path_receipt_is_verified_before_resume(self):
        extra="python/src/adxl355/adapters/spidev.py"
        metadata={**META,
                  "fleet_allowed_paths":json.dumps([extra])}
        doc={**DOC, "manifest":{**DOC["manifest"],
                                "allowed_paths":[META["fleet_allowed_path"],extra]}}
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/"worker.json"
            receipt.write_text(json.dumps(doc))
            with patch.object(queue.poller,"controller_receipt",return_value=receipt):
                self.assertEqual(queue.receipt_for(metadata),doc)
                malformed={**metadata,"fleet_allowed_paths":'[".github/config.yml"]'}
                with self.assertRaises(ValueError):
                    queue.receipt_for(malformed)

    def test_profile_selections_bound_to_immutable_worker_receipt(self):
        selected={**META,
                  "fleet_model":"opencode/nemotron-3-ultra-free",
                  "fleet_profile":"python-ruff",
                  "fleet_tool_profile":"python-docs",
                  "fleet_integrations":'["context7"]'}
        manifest={**DOC["manifest"],
                  "model":"opencode/nemotron-3-ultra-free",
                  "profile":"python-ruff",
                  "tool_profile":"python-docs",
                  "integrations":["context7"]}
        document={**DOC,"manifest":manifest}
        with tempfile.TemporaryDirectory() as directory:
            receipt=Path(directory)/"receipt.json"
            receipt.write_text(json.dumps(document))
            with patch.object(queue.poller,"controller_receipt",return_value=receipt):
                self.assertEqual(queue.receipt_for(selected),document)
                for change in (
                    {"fleet_model":"opencode/big-pickle"},
                    {"fleet_profile":"security-semgrep"},
                    {"fleet_tool_profile":"context7-mcp"},
                    {"fleet_integrations":'["codacy"]'},
                ):
                    with self.subTest(change=change),self.assertRaises(ValueError):
                        queue.receipt_for({**selected,**change})

    def test_post_dispatch_metadata_cannot_enable_or_disable_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"receipt.json"
            with patch.object(queue.poller, "controller_receipt", return_value=path):
                for original, altered in ((False, True), (True, False)):
                    with self.subTest(original=original, altered=altered):
                        doc={**DOC,"safe_base_advance_policy":original,
                             "target_account":"oaslananka",
                             "worker_account":"MakerParsDev",
                             "external_pr_only":True}
                        path.write_text(json.dumps(doc))
                        source={**META,"fleet_allow_safe_base_advance":original,
                                "fleet_external_pr_only":True,
                                "fleet_auto_merge":False}
                        self.assertEqual(queue.receipt_for(source),doc)
                        with self.assertRaisesRegex(ValueError,"original dispatch"):
                            queue.receipt_for({**source,
                                               "fleet_allow_safe_base_advance":altered})

    def test_dispatch_records_policy_before_worker_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/"receipt.json"
            original={**DOC,"safe_base_advance_policy":True,
                      "target_account":"oaslananka",
                      "worker_account":"MakerParsDev",
                      "external_pr_only":True}
            issue={**ISSUE,"metadata":{**META,
                "fleet_external_pr_only":True,
                "fleet_auto_merge":False,
                "fleet_allow_safe_base_advance":True}}
            seen=[]
            def execute(argv,timeout=30):
                seen.append(argv)
                if "--dispatch-only" in argv:
                    path.write_text(json.dumps(original))
                    return json.dumps({
                        "status":"WORKER_DISPATCHED","run_id":"12345",
                        "worker_repo":"MakerParsDev/oc-test",
                        "target_repo":"oaslananka-dev/oc-test",
                        "target_issue":"81","checkout_sha":"a"*40})
                return ""
            with patch.object(queue.handoff,"read_issue",return_value=issue),patch.object(
                queue.poller,"controller_receipt",return_value=path),patch.object(
                queue,"execute",side_effect=execute):
                self.assertEqual(queue.dispatch_one(issue,PID,True,root),"dispatched")
            dispatched=[a for a in seen if "--dispatch-only" in a]
            self.assertEqual(len(dispatched),1)
            self.assertIn("--safe-base-advance-policy",dispatched[0])
            self.assertNotIn("--allow-safe-base-advance",dispatched[0])

    def test_original_dispatch_opt_in_is_forwarded_on_resume_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"r.json"
            path.write_text(json.dumps({**DOC, "safe_base_advance_policy":True,
                                        "target_account":"oaslananka",
                                        "worker_account":"MakerParsDev",
                                        "external_pr_only":True}))
            meta={**PENDING,"fleet_external_pr_only":True,
                  "fleet_auto_merge":False,
                  "fleet_allow_safe_base_advance":True}
            task={**ISSUE,"status":"in_progress","metadata":meta}
            seen=[]
            def execute(args,timeout=30):
                seen.append(args)
                if args[:2]==["gh","api"]:
                    return json.dumps({**RUN,"status":"completed","conclusion":"success"})
                if "resume" in args:
                    return json.dumps({"status":"PR_CREATED","run_id":"12345",
                                       "url":"https://github.com/oaslananka-dev/oc-test/pull/93"})
                return ""
            with patch.object(queue.handoff,"read_issue",return_value=task),patch.object(
                    queue.poller,"controller_receipt",return_value=path),patch.object(
                    queue,"execute",side_effect=execute):
                self.assertEqual(queue.resume_one(task,PID,True),"published")
            resume=[cmd for cmd in seen if "resume" in cmd]
            self.assertEqual(len(resume),1)
            self.assertIn("--safe-base-advance-policy",resume[0])
            self.assertIn("--allow-safe-base-advance",resume[0])

    def test_safe_base_advance_is_not_added_for_non_opt_in_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"r.json"
            path.write_text(json.dumps(DOC))
            task={**ISSUE,"status":"in_progress","metadata":PENDING}
            seen=[]
            def execute(argv,timeout=30):
                seen.append(argv)
                if argv[:2]==["gh","api"]:
                    return json.dumps({**RUN,"status":"completed","conclusion":"success"})
                if "resume" in argv:
                    return json.dumps({"status":"PR_CREATED","run_id":"12345",
                                       "url":"https://github.com/oaslananka-dev/oc-test/pull/93"})
                return ""
            with patch.object(queue.handoff,"read_issue",return_value=task),patch.object(
                queue.poller,"controller_receipt",return_value=path),patch.object(
                queue,"execute",side_effect=execute):
                self.assertEqual(queue.resume_one(task,PID,True),"published")
            commands=[x for x in seen if "resume" in x]
            self.assertEqual(len(commands),1)
            self.assertNotIn("--allow-safe-base-advance",commands[0])

    def test_double_dispatch_stays_claimed_not_reissued(self):
        with tempfile.TemporaryDirectory() as d:
            claim=Path(d)/(KEY+".claim");claim.write_text(KEY)
            with patch.object(queue.handoff,"read_issue",return_value=ISSUE),patch.object(
                queue.poller,"controller_receipt",return_value=Path(d)/"missing"),patch.object(
                queue,"execute") as ex:
                self.assertEqual(queue.dispatch_one(ISSUE,PID,True,Path(d)),"already_claimed")
                ex.assert_not_called()

    def test_completed_run_publishes_only_original_receipt_and_same_pr(self):
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/"r.json";receipt.write_text(json.dumps(DOC))
            m={**ISSUE,"status":"in_progress","metadata":PENDING}
            actions=[]
            def command(argv,timeout=30):
                actions.append(argv)
                if argv[:2]==["gh","api"]:
                    return json.dumps({**RUN,"status":"completed","conclusion":"success"})
                if "resume" in argv:
                    return json.dumps({"status":"PR_CREATED","run_id":"12345",
                        "url":"https://github.com/oaslananka-dev/oc-test/pull/93"})
                return ""
            with patch.object(queue.handoff,"read_issue",return_value=m),patch.object(
                queue.poller,"controller_receipt",return_value=receipt),patch.object(
                queue,"execute",side_effect=command):
                self.assertEqual(queue.resume_one(m,PID,True),"published")
            self.assertEqual(sum("resume" in a for a in actions),1)
            self.assertEqual(sum("run" in a for a in actions),0)
            self.assertTrue(any("fleet_pull_request" in a for a in actions))
            self.assertTrue(any("in_review" in a for a in actions))

    def test_in_progress_worker_never_attempts_publish_or_metadata_writes(self):
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/"r.json";receipt.write_text(json.dumps(DOC))
            m={**ISSUE,"status":"in_progress","metadata":PENDING}
            def command(argv,timeout=30):
                if argv[:2]==["gh","api"]:return json.dumps(RUN)
                raise AssertionError("Only worker status queries permitted")
            with patch.object(queue.handoff,"read_issue",return_value=m),patch.object(
                queue.poller,"controller_receipt",return_value=receipt),patch.object(
                queue,"execute",side_effect=command) as call:
                self.assertEqual(queue.resume_one(m,PID,True),"pending")
                call.assert_called_once()

    def test_worker_failure_blocks_without_retry_or_publishing(self):
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/"r.json";receipt.write_text(json.dumps(DOC))
            m={**ISSUE,"status":"in_progress","metadata":PENDING}
            actions=[]
            def command(argv,timeout=30):
                actions.append(argv)
                if argv[:2]==["gh","api"]:
                    return json.dumps({**RUN,"status":"completed","conclusion":"failure"})
                return ""
            with patch.object(queue.handoff,"read_issue",return_value=m),patch.object(
                queue.poller,"controller_receipt",return_value=receipt),patch.object(
                queue,"execute",side_effect=command):
                self.assertEqual(queue.resume_one(m,PID,True),"failed")
            self.assertTrue(any("worker_failed" in a for a in actions))
            self.assertTrue(any("blocked" in a for a in actions))
            self.assertFalse(any("resume" in a for a in actions))

    def test_claim_recovery_read_only_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/(KEY+".claim")).write_text(KEY)
            receipt=root/"r.json";receipt.write_text(json.dumps(DOC))
            with patch.object(queue,"listing",return_value=[ISSUE]),patch.object(
                queue.poller,"controller_receipt",return_value=receipt),patch.object(
                queue,"execute") as command:
                self.assertEqual(queue.recover_claims(PID,root,False),1)
                command.assert_not_called()
            with patch.object(queue,"listing",return_value=[ISSUE]),patch.object(
                queue.poller,"controller_receipt",return_value=receipt),patch.object(
                queue,"execute",return_value="") as command:
                self.assertEqual(queue.recover_claims(PID,root,True),1)
                self.assertEqual(command.call_count,4)

    def test_unknown_claim_never_dispatches_or_marks_run(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/(KEY+".claim")).write_text(KEY)
            with patch.object(queue,"listing",return_value=[ISSUE]),patch.object(
                queue.poller,"controller_receipt",return_value=root/"missing"),patch.object(
                queue,"execute") as command:
                self.assertEqual(queue.recover_claims(PID,root,True),0)
                command.assert_not_called()

    def test_cycle_holds_merges_while_original_worker_pending(self):
        with patch.object(queue,"recover_claims",return_value=0),patch.object(
            queue,"listing",return_value=[{**ISSUE,"status":"in_progress","metadata":PENDING}]),patch.object(
            queue,"resume_one",return_value="pending"),patch.object(
            queue.poller.finalizer,"poll") as finalize,patch.object(
            queue.poller,"poll",return_value="empty") as dispatch:
            self.assertEqual(queue.cycle(PID,True,Path("/tmp/no-claims"),4),"empty")
            finalize.assert_not_called()
            self.assertIs(dispatch.call_args.kwargs["dispatch_handler"],queue.dispatch_one)

    def test_one_unreconciled_worker_does_not_block_new_dispatch(self):
        with patch.object(queue,"recover_claims",return_value=0),patch.object(
            queue,"listing",return_value=[{**ISSUE,"status":"in_progress","metadata":PENDING}]),patch.object(
            queue,"resume_one",side_effect=ValueError("original SHA moved")),patch.object(
            queue.poller.finalizer,"poll") as finalize,patch.object(
            queue.poller,"poll",return_value="dispatched") as dispatch:
            self.assertEqual(queue.cycle(PID,True,Path("/tmp/none"),4),"dispatched")
            finalize.assert_not_called()
            dispatch.assert_called_once()

    def test_cycle_releases_merges_after_worker_published(self):
        with patch.object(queue,"recover_claims",return_value=0),patch.object(
            queue,"listing",return_value=[{**ISSUE,"status":"in_progress","metadata":PENDING}]),patch.object(
            queue,"resume_one",return_value="published"),patch.object(
            queue.poller.finalizer,"poll",return_value="completed") as finalize,patch.object(
            queue.poller,"poll",return_value="empty"):
            self.assertEqual(queue.cycle(PID,True,Path("/tmp/no-claims"),4),"empty")
            finalize.assert_called_once()

    def test_controller_dispatch_only_persists_receipt_without_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/"r.json"
            manifest={"task_id":"gh-81-abc123","repository":"oaslananka-dev/oc-test",
                      "checkout_sha":"a"*40,"issue_key":"GH-81"}
            argv=["oc_fleet_issue.py","run","--repo","oaslananka-dev/oc-test",
                  "--issue","81","--path","playground/fleet/test.json",
                  "--dispatch-only"]
            capture=io.StringIO()
            with patch.object(sys,"argv",argv),patch.object(
                controller,"identity",return_value={}),patch.object(
                controller,"authorized_worker_repo",return_value="MakerParsDev/oc-test"),patch.object(
                controller,"repo_issue",return_value=("main","a"*40,"Fix issue safely")),patch.object(
                controller,"receipt",return_value=receipt),patch.object(
                controller,"retry_receipt",return_value=[]),patch.object(
                controller,"allow_target",return_value=["oaslananka-dev/oc-test"]),patch.object(
                controller,"manifest_for",return_value=manifest),patch.object(
                controller,"dispatch",return_value=12345),patch.object(
                controller,"await_run") as await_run,patch.object(
                controller,"publish") as publish,contextlib.redirect_stdout(capture):
                controller.main()
            self.assertEqual(json.loads(receipt.read_text())["run_id"],12345)
            self.assertIn('"status": "WORKER_DISPATCHED"',capture.getvalue())
            await_run.assert_not_called()
            publish.assert_not_called()

    def test_dispatch_only_rejects_preview_or_resume(self):
        for mode,extra in (("resume",[]),("run",["--dry-run"])):
            with self.subTest(mode=mode),patch.object(
                sys,"argv",["oc_fleet_issue.py",mode,"--repo","oaslananka-dev/oc-test",
                            "--issue","81","--dispatch-only",*extra]),patch.object(
                controller,"identity") as identity:
                with self.assertRaisesRegex(ValueError,"Dispatch-only"):
                    controller.main()
                identity.assert_not_called()

    def test_malicious_controller_output_never_updates_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"r.json"
            output=json.dumps({"status":"WORKER_DISPATCHED","run_id":"111",
                              "worker_repo":"MakerParsDev/oc-test",
                              "target_repo":"oaslananka-dev/oc-test",
                              "target_issue":"81"})
            def invalid(argv,timeout=30):
                path.write_text(json.dumps(DOC))
                return output
            with patch.object(queue.handoff,"read_issue",return_value=ISSUE),patch.object(
                queue.poller,"controller_receipt",return_value=path),patch.object(
                queue,"execute",side_effect=invalid) as e:
                with self.assertRaisesRegex(ValueError,"binding"):
                    queue.dispatch_one(ISSUE,PID,True,Path(d))
                self.assertEqual(e.call_count,1)


if __name__=="__main__":
    unittest.main()
