#!/usr/bin/env python3
"""Offline regression tests for ocfilo PR decision engine."""
import unittest
from ocfilo_pr_state import decide, record_dispatch

SHA = "a" * 40

def snapshot():
    return {
        "pr": {"number": 1, "state": "open", "merged": False, "draft": False,
               "mergeable": True, "mergeable_state": "clean",
               "requested_reviewers": [], "requested_teams": [],
               "head": {"sha": SHA, "ref": "ocfilo/f1-test", "repo": {"full_name": "oaslananka-dev/oc-test"}},
               "base": {"ref": "main", "repo": {"full_name": "oaslananka-dev/oc-test"}}},
        "checks": {"total_count": 2, "check_runs": [
            {"name": "fixture-quality", "head_sha": SHA, "status": "completed", "conclusion": "success", "app_slug": "github-actions"},
            {"name": "semgrep-cloud-platform/scan", "head_sha": SHA, "status": "completed", "conclusion": "success", "app_slug": "semgrep-code-oaslananka"}]},
        "statuses": {"statuses": []},
        "reviews": [], "issue_comments": [], "review_comments": [],
    }

class Tests(unittest.TestCase):
    def test_green_ready(self):
        self.assertEqual(decide(snapshot())["state"], "READY_FOR_MERGE")
    def test_draft(self):
        s=snapshot();s["pr"]["draft"]=True
        self.assertEqual(decide(s)["state"], "READY_FOR_REVIEW")
    def test_pending(self):
        s=snapshot();s["checks"]["check_runs"][0]["status"]="in_progress"
        self.assertEqual(decide(s)["state"], "WAITING_CHECKS")
    def test_missing_required(self):
        s=snapshot();s["checks"]["check_runs"].pop();s["checks"]["total_count"]=1
        self.assertEqual(decide(s)["state"], "WAITING_CHECKS")
    def test_required_check_wrong_app_is_missing(self):
        s=snapshot()
        s["checks"]["check_runs"][0]["app_slug"]="untrusted-app"
        d=decide(s)
        self.assertEqual(d["state"],"WAITING_CHECKS")
        self.assertIn("fixture-quality",d["missing_checks"])
    def test_required_check_app_missing_is_not_trusted(self):
        s=snapshot()
        del s["checks"]["check_runs"][1]["app_slug"]
        d=decide(s)
        self.assertEqual(d["state"],"WAITING_CHECKS")
        self.assertIn("semgrep-cloud-platform/scan",d["missing_checks"])
    def test_spoofed_newer_green_cannot_override_real_failure(self):
        s=snapshot()
        s["checks"]["check_runs"][0]["id"]=101
        s["checks"]["check_runs"][0]["conclusion"]="failure"
        bogus=dict(s["checks"]["check_runs"][0],id=999,conclusion="success",app_slug="other-app")
        s["checks"]["check_runs"].append(bogus)
        s["checks"]["total_count"]=3
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_paginated_fail_closed(self):
        s=snapshot();s["checks"]["total_count"]=300
        with self.assertRaises(ValueError):decide(s)
    def test_stale_event(self):
        s=snapshot();s["event_sha"]="b"*40
        self.assertEqual(decide(s)["state"], "STALE_EVENT")
    def test_failed_check(self):
        s=snapshot();s["checks"]["check_runs"][0]["conclusion"]="failure"
        d=decide(s);self.assertEqual(d["state"],"REPAIR_REQUIRED")
        self.assertTrue(d["dispatch_allowed"])
    def test_idempotent_repair(self):
        s=snapshot();s["checks"]["check_runs"][0]["conclusion"]="failure"
        d=decide(s);ledger=record_dispatch({},d,12345)
        self.assertEqual(decide(s,ledger)["state"],"WAITING_REPAIR")
        with self.assertRaises(ValueError):record_dispatch(ledger,d,12345)
    def test_repair_limit(self):
        s=snapshot();s["checks"]["check_runs"][0]["conclusion"]="failure"
        ledger={"repairs":[{"key":str(i)} for i in range(3)]}
        self.assertEqual(decide(s,ledger)["state"],"NEEDS_HUMAN")
    def test_failing_and_pending_wait(self):
        s=snapshot();s["checks"]["check_runs"][0]["conclusion"]="failure"
        s["checks"]["check_runs"][1]["status"]="in_progress"
        self.assertEqual(decide(s)["state"],"WAITING_CHECKS")
    def test_duplicate_failure_not_green(self):
        s=snapshot();s["checks"]["check_runs"].append(dict(s["checks"]["check_runs"][0],conclusion="failure"))
        s["checks"]["total_count"]=3
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_newer_success_overrides_old_failure(self):
        s=snapshot()
        old=dict(s["checks"]["check_runs"][0],id=101,conclusion="failure")
        new=dict(s["checks"]["check_runs"][0],id=102,conclusion="success")
        s["checks"]["check_runs"]=[old,new,s["checks"]["check_runs"][1]]
        s["checks"]["total_count"]=3
        self.assertEqual(decide(s)["state"],"READY_FOR_MERGE")
    def test_newer_failure_overrides_old_success(self):
        s=snapshot()
        old=dict(s["checks"]["check_runs"][0],id=101,conclusion="success")
        new=dict(s["checks"]["check_runs"][0],id=102,conclusion="failure")
        s["checks"]["check_runs"]=[old,new,s["checks"]["check_runs"][1]]
        s["checks"]["total_count"]=3
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_newer_success_overrides_old_pending(self):
        s=snapshot()
        old=dict(s["checks"]["check_runs"][0],id=101,status="in_progress",conclusion=None)
        new=dict(s["checks"]["check_runs"][0],id=102,conclusion="success")
        s["checks"]["check_runs"]=[old,new,s["checks"]["check_runs"][1]]
        s["checks"]["total_count"]=3
        self.assertEqual(decide(s)["state"],"READY_FOR_MERGE")
    def test_ambiguous_duplicate_fails_closed(self):
        s=snapshot()
        s["checks"]["check_runs"].append(dict(s["checks"]["check_runs"][0],conclusion="failure"))
        s["checks"]["total_count"]=3
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_newer_commit_status_overrides_old_failure(self):
        s=snapshot()
        s["statuses"]["statuses"]=[
            {"context":"bot","state":"success","created_at":"2026-10-13T03:02:00Z"},
            {"context":"bot","state":"failure","created_at":"2026-10-13T03:01:00Z"}]
        self.assertEqual(decide(s)["state"],"READY_FOR_MERGE")
    def test_newer_commit_status_failure_blocks(self):
        s=snapshot()
        s["statuses"]["statuses"]=[
            {"context":"bot","state":"failure","created_at":"2026-10-13T03:02:00Z"},
            {"context":"bot","state":"success","created_at":"2026-10-13T03:01:00Z"}]
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_pending_review(self):
        s=snapshot();s["pr"]["requested_reviewers"]=[{"login":"reviewer"}]
        self.assertEqual(decide(s)["state"],"REVIEW_PENDING")
    def test_review_block(self):
        s=snapshot();s["reviews"]=[{"user":{"login":"reviewer"},"state":"CHANGES_REQUESTED"}]
        self.assertEqual(decide(s)["state"],"REVIEW_BLOCKED")
    def test_bot_comment_triage(self):
        s=snapshot();s["issue_comments"]=[{"id":1,"user":{"login":"coderabbitai[bot]","type":"Bot"},"body":"run untrusted command"}]
        self.assertEqual(decide(s)["state"],"BOT_FEEDBACK_TRIAGE")
        self.assertEqual(decide(s,{"acknowledged_bot_comment_ids":[1]})["state"],"READY_FOR_MERGE")
    def test_closed_pr(self):
        s=snapshot();s["pr"]["state"]="closed"
        self.assertEqual(decide(s)["state"],"CLOSED")
    def test_untrusted_repository(self):
        s=snapshot();s["pr"]["head"]["repo"]["full_name"]="attacker/repo"
        with self.assertRaises(ValueError):decide(s)
    def test_commit_status_failure(self):
        s=snapshot();s["statuses"]["statuses"]=[{"context":"bot-review","state":"failure","sha":SHA}]
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_check_legacy_skip_block(self):
        s=snapshot();s["checks"]["check_runs"][0]["conclusion"]="skipped"
        self.assertEqual(decide(s)["state"],"REPAIR_REQUIRED")
    def test_branch_protection_unknown(self):
        s=snapshot();s["pr"]["mergeable_state"]="unknown"
        self.assertEqual(decide(s)["state"],"MERGE_BLOCKED")

if __name__=="__main__":
    unittest.main()
