#!/usr/bin/env python3
"""Regress read-only F1 merge-policy assurance classifications."""
import copy
import unittest
from ocfilo_merge_policy import audit

REPO = "oaslananka-dev/oc-test"
SHA = "a"*40
def sample():
    pr = {"number":1, "state":"open", "merged":False,
          "base":{"ref":"main"},
          "head":{"sha":SHA,"repo":{"full_name":REPO}}}
    branch = {"name":"main", "protected":False}
    decision = {"repository":REPO,"pr_number":1,"head_sha":SHA,
                "state":"READY_FOR_MERGE","dispatch_allowed":False}
    return [pr,branch,[],decision]

class PolicyAuditTests(unittest.TestCase):
    def test_unprotected_main_is_not_policy_verified(self):
        p,b,r,t=sample()
        result=audit(p,b,r,t)
        self.assertEqual(result["policy_status"],"F1_CHECKS_PASS_BUT_NO_ENFORCED_GITHUB_POLICY")
        self.assertFalse(result["github_policy_verified"])
    def test_no_auto_merge_ever(self):
        self.assertFalse(audit(*sample())["automatic_merge_allowed"])
    def test_protection_requires_rule_detail_review(self):
        p,b,r,t=sample();b["protected"]=True
        self.assertEqual(audit(p,b,r,t)["policy_status"],"GITHUB_POLICY_DETAILS_NEED_VERIFICATION")
    def test_active_ruleset_needs_detail_review(self):
        p,b,r,t=sample();r.append({"enforcement":"active"})
        self.assertEqual(audit(p,b,r,t)["policy_status"],"ADDITIONAL_ACTIVE_RULESETS_REQUIRE_REVIEW")
    @staticmethod
    def protected_evidence():
        return {
            "required_status_checks": {
                "strict": False,
                "checks": [{"context": "baseline", "app_id": 15368}],
            },
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
            },
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
            "lock_branch": {"enabled": False},
        }

    def test_exact_policy_is_verified_with_protection_get(self):
        p,b,r,t=sample();b["protected"]=True
        result=audit(p,b,r,t,self.protected_evidence())
        self.assertEqual(result["policy_status"],"GITHUB_POLICY_VERIFIED_FOR_F1")
        self.assertTrue(result["github_policy_verified"])
        self.assertFalse(result["automatic_merge_allowed"])
    def test_missing_admin_permission_is_unverified(self):
        p,b,r,t=sample();b["protected"]=True
        result=audit(p,b,r,t)
        self.assertFalse(result["protection_details_supplied"])
        self.assertFalse(result["github_policy_verified"])
    def test_spoofed_required_app_denied(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["required_status_checks"]["checks"][0]["app_id"]=123
        self.assertEqual(audit(p,b,r,t,x)["policy_status"],"GITHUB_POLICY_CONFIGURATION_MISMATCH")
    def test_missing_baseline_denied(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["required_status_checks"]["checks"]=[]
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_extra_required_check_does_not_match_approved_policy(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["required_status_checks"]["checks"].append({"context":"other","app_id":15368})
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_admin_bypass_denied(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["enforce_admins"]["enabled"]=False
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_force_push_denied(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["allow_force_pushes"]["enabled"]=True
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_branch_deletion_denied(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["allow_deletions"]["enabled"]=True
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_review_requirement_changed(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["required_pull_request_reviews"]["required_approving_review_count"]=1
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_strict_rule_changed(self):
        p,b,r,t=sample();b["protected"]=True
        x=self.protected_evidence()
        x["required_status_checks"]["strict"]=True
        self.assertFalse(audit(p,b,r,t,x)["github_policy_verified"])
    def test_active_extra_ruleset_requires_review(self):
        p,b,r,t=sample();b["protected"]=True
        r.append({"enforcement":"active"})
        x=self.protected_evidence()
        result=audit(p,b,r,t,x)
        self.assertEqual(result["policy_status"],"ADDITIONAL_ACTIVE_RULESETS_REQUIRE_REVIEW")
        self.assertFalse(result["github_policy_verified"])
    def test_green_branch_but_pending_f1_not_verified_for_merge(self):
        p,b,r,t=sample();b["protected"]=True;t["state"]="WAITING_CHECKS"
        x=self.protected_evidence()
        result=audit(p,b,r,t,x)
        self.assertEqual(result["policy_status"],"F1_CHECKS_NOT_READY")
        self.assertTrue(result["approved_protection_configuration_verified"])
        self.assertFalse(result["github_policy_verified"])
    def test_closed_pr_not_verified_for_merge(self):
        p,b,r,t=sample();b["protected"]=True;p["state"]="closed"
        result=audit(p,b,r,t,self.protected_evidence())
        self.assertEqual(result["policy_status"],"CLOSED")
        self.assertFalse(result["github_policy_verified"])
    def test_draft_technical_not_ready(self):
        p,b,r,t=sample();t["state"]="READY_FOR_REVIEW"
        self.assertEqual(audit(p,b,r,t)["policy_status"],"F1_CHECKS_NOT_READY")
    def test_failing_check_not_ready(self):
        p,b,r,t=sample();t["state"]="REPAIR_REQUIRED";t["dispatch_allowed"]=True
        self.assertEqual(audit(p,b,r,t)["policy_status"],"F1_CHECKS_NOT_READY")
    def test_stale_sha_rejected(self):
        p,b,r,t=sample();t["head_sha"]="b"*40
        with self.assertRaises(ValueError): audit(p,b,r,t)
    def test_foreign_repo_rejected(self):
        p,b,r,t=sample();p["head"]["repo"]["full_name"]="attacker/test"
        with self.assertRaises(ValueError): audit(p,b,r,t)
    def test_pr_mismatch_rejected(self):
        p,b,r,t=sample();t["pr_number"]=2
        with self.assertRaises(ValueError): audit(p,b,r,t)
    def test_protection_status_unknown_denied(self):
        p,b,r,t=sample();del b["protected"]
        with self.assertRaises(ValueError): audit(p,b,r,t)
    def test_incomplete_rulesets_denied(self):
        p,b,r,t=sample();r.append({"name":"rules lacking enforcement status"})
        with self.assertRaises(ValueError): audit(p,b,r,t)
    def test_closed(self):
        p,b,r,t=sample();p["state"]="closed"
        self.assertEqual(audit(p,b,r,t)["policy_status"],"CLOSED")
    def test_no_repair_dispatch_in_audit(self):
        self.assertEqual(audit(*sample())["action"],"REPORT_ONLY")

if __name__=="__main__":
    unittest.main()
