#!/usr/bin/env python3
"""Offline tests for event routing; no GitHub credentials or network."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import ocfilo_observer_route as router
import ocfilo_pr_observer as observer
from ocfilo_observer_route import route, REPO, SEMGREP_APP, SEMGREP_CHECK, QUALITY_WORKFLOW

SHA1 = "a"*40
SHA2 = "b"*40

def current(number=1, sha=SHA1, state="open"):
    return {"number": number, "state": state,
            "head": {"sha": sha, "repo": {"full_name": REPO}}}

def pr_event(number=1, sha=SHA1, action="synchronize"):
    return {"action": action, "pull_request": {"number":number, "head":{"sha":sha}}}

def quality_event(sha=SHA1):
    return {"workflow_run": {"name":QUALITY_WORKFLOW, "status":"completed",
                              "head_sha":sha, "head_repository":{"full_name":REPO}}}

def semgrep_event(sha=SHA1, numbers=None):
    return {"check_run": {"name":SEMGREP_CHECK, "status":"completed",
                           "app":{"slug":SEMGREP_APP}, "head_sha":sha,
                           "pull_requests":[{"number": n} for n in (numbers or [])]}}

class EventRoutingTests(unittest.TestCase):
    def test_main_emits_verified_head_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            payload=Path(temp)/"event.json"
            output=Path(temp)/"output.txt"
            payload.write_text(json.dumps({}))
            argv=["router.py","workflow_dispatch",str(payload),"1"]
            with patch.object(sys,"argv",argv), patch.dict(os.environ,{"GITHUB_OUTPUT":str(output)}), patch.object(router,"live_pr",return_value=current()):
                router.main()
            values=output.read_text()
            self.assertIn("should_observe=true\\n",values)
            self.assertIn("matched_sha="+SHA1+"\\n",values)
    def test_main_does_not_emit_sha_for_unrelated_pr(self):
        with tempfile.TemporaryDirectory() as temp:
            payload=Path(temp)/"event.json"
            output=Path(temp)/"output.txt"
            payload.write_text(json.dumps(pr_event(number=2)))
            argv=["router.py","pull_request",str(payload),"1"]
            with patch.object(sys,"argv",argv),patch.dict(os.environ,{"GITHUB_OUTPUT":str(output)}),patch.object(router,"live_pr",return_value=current()):
                router.main()
            values=output.read_text()
            self.assertIn("should_observe=false\\n",values)
            self.assertIn("matched_sha=\\n",values)
    def test_observer_rejects_changed_sha_before_collecting_checks(self):
        live=current()
        live.update({"merged":False,"draft":False,"mergeable":True,"mergeable_state":"clean"})
        with patch.object(observer,"get",return_value=live) as call:
            with self.assertRaisesRegex(ValueError,"HEAD changed"):
                observer.collect(1,SHA2)
            self.assertEqual(call.call_count,1)
    def test_observer_rejects_missing_routed_sha(self):
        with patch.object(observer,"get",return_value=current()) as call:
            with self.assertRaisesRegex(ValueError,"HEAD changed"):
                observer.collect(1,None)
            self.assertEqual(call.call_count,1)
    def test_manual_dispatch_current(self):
        self.assertTrue(route("workflow_dispatch", {},1,current())[0])
    def test_does_not_observe_closed(self):
        self.assertFalse(route("workflow_dispatch",{},1,current(state="closed"))[0])
    def test_other_pr_pull_event(self):
        self.assertFalse(route("pull_request",pr_event(number=2),1,current())[0])
    def test_matched_pull_event(self):
        self.assertTrue(route("pull_request",pr_event(),1,current())[0])
    def test_stale_pull_event(self):
        self.assertFalse(route("pull_request",pr_event(sha=SHA2),1,current())[0])
    def test_unsupported_pull_action(self):
        self.assertFalse(route("pull_request",pr_event(action="edited"),1,current())[0])
    def test_quality_workflow_on_current_sha(self):
        self.assertTrue(route("workflow_run",quality_event(),1,current())[0])
    def test_quality_workflow_old_sha(self):
        self.assertFalse(route("workflow_run",quality_event(SHA2),1,current())[0])
    def test_wrong_quality_workflow(self):
        e=quality_event();e["workflow_run"]["name"]="untrusted-job"
        self.assertFalse(route("workflow_run",e,1,current())[0])
    def test_wrong_run_repository(self):
        e=quality_event();e["workflow_run"]["head_repository"]["full_name"]="attacker/repo"
        self.assertFalse(route("workflow_run",e,1,current())[0])
    def test_noncompleted_run(self):
        e=quality_event();e["workflow_run"]["status"]="in_progress"
        self.assertFalse(route("workflow_run",e,1,current())[0])
    def test_semgrep_on_current_sha(self):
        self.assertTrue(route("check_run",semgrep_event(numbers=[1]),1,current())[0])
    def test_unrelated_semgrep_sha(self):
        self.assertFalse(route("check_run",semgrep_event(SHA2),1,current())[0])
    def test_semgrep_wrong_app(self):
        e=semgrep_event();e["check_run"]["app"]["slug"]="untrusted"
        self.assertFalse(route("check_run",e,1,current())[0])
    def test_semgrep_wrong_name(self):
        e=semgrep_event();e["check_run"]["name"]="baseline"
        self.assertFalse(route("check_run",e,1,current())[0])
    def test_semgrep_other_linked_pr(self):
        self.assertFalse(route("check_run",semgrep_event(numbers=[2]),1,current())[0])
    def test_semgrep_no_association_can_match_sha(self):
        self.assertTrue(route("check_run",semgrep_event(numbers=[]),1,current())[0])
    def test_unknown_event_skip(self):
        self.assertFalse(route("push",{},1,current())[0])
    def test_invalid_sha_denied(self):
        with self.assertRaises(ValueError):
            route("workflow_dispatch",{},1,current(sha="unsafe"))
    def test_foreign_repository_skip(self):
        c=current();c["head"]["repo"]["full_name"]="attacker/repo"
        self.assertFalse(route("workflow_dispatch",{},1,c)[0])
    def test_invalid_pr_target_rejected(self):
        with self.assertRaises(ValueError):
            route("workflow_dispatch",{},3,current(number=3))
    def test_pr2_is_independently_matched(self):
        self.assertTrue(route("pull_request",pr_event(number=2,sha=SHA2),2,current(2,SHA2))[0])
        self.assertFalse(route("pull_request",pr_event(number=2,sha=SHA2),1,current(1,SHA1))[0])

if __name__=="__main__":
    unittest.main()
