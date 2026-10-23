#!/usr/bin/env python3
"""Offline negative-first native reviewer evidence contract tests."""
import json
import subprocess
import unittest
from unittest.mock import Mock, patch
from oc_fleet_approval_probe import (REPO, WORKFLOW, ENVIRONMENT,
                                      inspect, read_api)

RID, SHA, REVIEWER, ENV = "12345678", "a"*40, 99, 42
CONTROL = {"id": int(RID), "run_attempt": 1, "repository": {"full_name": REPO},
           "head_sha": SHA, "event": "workflow_dispatch", "head_branch": "main",
           "path": WORKFLOW, "triggering_actor": {"id": 100, "type": "User"}}
OK = {"state": "approved",
      "environments": [{"id": ENV, "name": ENVIRONMENT}],
      "user": {"id": REVIEWER, "type": "User", "login": "human"}}

ENV_CONFIG = {"id": ENV, "name": ENVIRONMENT, "protection_rules": [
    {"type": "required_reviewers", "prevent_self_review": True,
     "reviewers": [{"type": "User", "reviewer": {"id": REVIEWER}}]}]}


class ApprovalTests(unittest.TestCase):
    def check(self, run=None, reviews=None, environment=None):
        calls = []
        def fetch(path):
            calls.append(path)
            if path.endswith("/approvals"):
                return [OK] if reviews is None else reviews
            if path.endswith("/environments/" + ENVIRONMENT):
                return ENV_CONFIG if environment is None else environment
            return CONTROL if run is None else run
        return inspect(RID, SHA, REVIEWER, ENV, fetch), calls

    def test_review_is_not_authorization(self):
        result, calls = self.check()
        self.assertTrue(result["matching_approval_record_observed"])
        self.assertTrue(result["required_reviewer_configuration_observed"])
        for key in ("cancel_authorized", "can_cancel_workflow",
                    "target_run_manifest_bound", "environment_protection_verified",
                    "target_specific_approval_verified", "approval_freshness_verified"):
            self.assertFalse(result[key])
        self.assertEqual(calls, [f"repos/{REPO}/actions/runs/{RID}",
                                 f"repos/{REPO}/environments/{ENVIRONMENT}",
                                 f"repos/{REPO}/actions/runs/{RID}/approvals"])

    def test_wrong_control_run_never_checks_approvals(self):
        for bad in ({"id": int(RID)+1}, {"run_attempt": 2},
                    {"repository": {"full_name": "wrong/repo"}},
                    {"head_sha": "b"*40}, {"head_branch": "dev"},
                    {"event": "push"}, {"path": "malicious.yml"}):
            calls = []
            def fetch(path):
                calls.append(path)
                return {**CONTROL, **bad}
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                inspect(RID, SHA, REVIEWER, ENV, fetch)
            self.assertEqual(len(calls), 1)

    def test_wrong_approver_or_environment_not_an_approval(self):
        for record in ({**OK, "user": {"id": REVIEWER+1, "type": "User"}},
                       {**OK, "user": {"id": REVIEWER, "type": "Bot"}},
                       {**OK, "environments": [{"id": ENV+1, "name": ENVIRONMENT}]},
                       {**OK, "environments": [{"id": ENV, "name": "other"}]},
                       {**OK, "state": "pending"}):
            with self.subTest(record=record):
                value, _ = self.check(reviews=[record])
                self.assertFalse(value["matching_approval_record_observed"])
                self.assertFalse(value["cancel_authorized"])

    def test_rejection_or_malformed_history_fails_closed(self):
        with self.assertRaises(ValueError):
            self.check(reviews=[OK, {**OK, "state": "rejected"}])
        for records in ("approved", [{}], [{**OK, "environments": None}], [OK]*101):
            with self.subTest(records=str(records)[:20]), self.assertRaises(ValueError):
                self.check(reviews=records)

    def test_narrow_reviewer_policy_fail_closed(self):
        rule = ENV_CONFIG["protection_rules"][0]
        for bad in (
            {**ENV_CONFIG, "id": ENV+1},
            {**ENV_CONFIG, "name": "other"},
            {**ENV_CONFIG, "protection_rules": []},
            {**ENV_CONFIG, "protection_rules": [rule, rule]},
            {**ENV_CONFIG, "protection_rules": [{**rule, "prevent_self_review": False}]},
            {**ENV_CONFIG, "protection_rules": [{**rule, "reviewers": rule["reviewers"]*2}]},
            {**ENV_CONFIG, "protection_rules": [{**rule, "reviewers": [
                {"type": "User", "reviewer": {"id": REVIEWER+1}}]}]},
            {**ENV_CONFIG, "protection_rules": [{**rule, "reviewers": [
                {"type": "Team", "reviewer": {"id": REVIEWER}}]}]},
            {**ENV_CONFIG, "protection_rules": "invalid"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.check(environment=bad)

    def test_initiator_cannot_self_approve(self):
        for trigger in ({"id": REVIEWER, "type": "User"},
                        {"id": True, "type": "User"},
                        {"id": 100, "type": "Bot"}, None):
            with self.subTest(trigger=trigger), self.assertRaises(ValueError):
                self.check(run={**CONTROL, "triggering_actor": trigger})

    def test_invalid_trust_anchors_fail_before_network(self):
        for data in (("0", SHA, REVIEWER, ENV),
                     (RID, "fake", REVIEWER, ENV),
                     (RID, SHA, True, ENV),
                     (RID, SHA, REVIEWER, 0)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                inspect(*data, reader=lambda _: self.fail("must not query"))

    def test_only_authenticated_get_without_mutation(self):
        with patch("oc_fleet_approval_probe.subprocess.run",
                   return_value=Mock(stdout=json.dumps(CONTROL))) as runner:
            self.assertEqual(read_api(f"repos/{REPO}/actions/runs/{RID}")["id"], int(RID))
            self.assertEqual(runner.call_args.args[0],
                             ["gh", "api", f"repos/{REPO}/actions/runs/{RID}"])
        with patch("oc_fleet_approval_probe.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("gh", 15)):
            with self.assertRaises(subprocess.SubprocessError):
                read_api(f"repos/{REPO}/actions/runs/{RID}")


if __name__ == "__main__":
    unittest.main()
