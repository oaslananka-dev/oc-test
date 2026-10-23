#!/usr/bin/env python3
"""Synthetic trust anchors only: never send or approve cancellation."""
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from oc_fleet_cancel_admission import canonical
from oc_fleet_cancel_target_binding import inspect_binding
from oc_fleet_approval_probe import inspect as inspect_review
from oc_fleet_supervisor import REPO, WORKFLOW, cancel_proposal

NOW = datetime(2026, 10, 23, 19, 0, tzinfo=timezone.utc)
RUN, SHA, CTRL, CSHA = "39000009999", "a"*40, "39000007777", "b"*40
ENV, REVIEWER, REQUESTER = 42, 99, 100
TARGET = {
    "github": {"verified": True, "state": "nonterminal_at_github",
               "repository": REPO, "run_id": RUN, "attempt": "1",
               "head_sha": SHA, "workflow": WORKFLOW},
    "observation": {"run_id": RUN, "attempt": "1", "multica_issue": "OAS-12"}
}
RECEIPT = {"repository": REPO, "run_id": RUN, "attempt": "1", "head_sha": SHA,
           "multica_issue": "OAS-12", "task_id": "task-alpha"}
CONTROL = {"id": int(CTRL), "run_attempt": 1,
           "repository": {"full_name": REPO}, "head_sha": CSHA,
           "event": "workflow_dispatch", "head_branch": "main",
           "path": ".github/workflows/oc-fleet-cancel-control.yml",
           "triggering_actor": {"id": REQUESTER, "type": "User"}}
ENV_CONFIG = {"id": ENV, "name": "oc-fleet-cancel-review", "protection_rules": [
    {"type": "required_reviewers", "prevent_self_review": True,
     "reviewers": [{"type": "User", "reviewer": {"id": REVIEWER}}]}]}
APPROVED = {"state": "approved",
            "environments": [{"id": ENV, "name": "oc-fleet-cancel-review"}],
            "user": {"type": "User", "id": REVIEWER}}


def reviewed(path):
    if path.endswith("/approvals"):
        return [APPROVED]
    if path.endswith("/environments/oc-fleet-cancel-review"):
        return ENV_CONFIG
    return CONTROL


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.proposal = cancel_proposal(TARGET, NOW)
        self.manifest = {
            "schema_version": 1, "action": "review_cancel",
            "proposal_id": self.proposal["proposal_id"],
            "repository": REPO, "workflow": WORKFLOW,
            "run_id": RUN, "run_attempt": "1", "head_sha": SHA,
            "registered_multica_issue": "OAS-12", "task_id": "task-alpha",
            "control_run_id": CTRL, "control_head_sha": CSHA,
            "environment_id": ENV, "reviewer_id": REVIEWER,
            "requester_id": REQUESTER,
            "issued_at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at_utc": (NOW + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "executable": False,
        }
        self.probe = inspect_review(CTRL, CSHA, REVIEWER, ENV, reviewed)

    def binding(self, **kwargs):
        manifest = kwargs.pop("manifest", self.manifest)
        return inspect_binding(
            kwargs.pop("proposal", self.proposal),
            kwargs.pop("receipt", RECEIPT),
            manifest,
            kwargs.pop("probe", self.probe),
            pinned_manifest_digest=kwargs.pop(
                "digest", hashlib.sha256(canonical(self.manifest).encode()).hexdigest()),
            pinned_control_sha=kwargs.pop("sha", CSHA),
            pinned_reviewer_id=kwargs.pop("reviewer", REVIEWER),
            pinned_environment_id=kwargs.pop("environment", ENV),
            now=kwargs.pop("now", NOW), **kwargs)

    def test_matched_offline_binding_never_authorizes(self):
        result = self.binding()
        self.assertTrue(result["target_binding_matches_pinned_inputs"])
        self.assertTrue(result["review_history_matches_pinned_inputs"])
        for key in ("cancel_requested", "execution_enabled", "cancel_authorized",
                    "trusted_manifest_origin_verified",
                    "production_identity_authenticated",
                    "target_specific_approval_verified",
                    "approval_freshness_verified"):
            self.assertIs(result[key], False)

    def test_manifest_fields_fail_closed(self):
        for change in (
            {"repository": "attacker/target"}, {"workflow": "other.yml"},
            {"run_id": "39000009998"}, {"run_attempt": "2"},
            {"head_sha": "f"*40}, {"registered_multica_issue": "OAS-10"},
            {"task_id": "task-beta"}, {"proposal_id": "0"*64},
            {"control_run_id": "39000007778"},
            {"control_head_sha": "e"*40}, {"environment_id": ENV+1},
            {"reviewer_id": REVIEWER+1}, {"requester_id": REVIEWER},
            {"requester_id": True}, {"executable": True},
            {"action": "force_cancel"}, {"schema_version": True},
            {"control_run_id": "invalid"},
            {"issued_at_utc": "2026-10-23T18:59:59Z"},
            {"expires_at_utc": "2026-10-23T19:06:00Z"},
            {"expires_at_utc": "2026-10-23T19:00:00Z"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.binding(manifest={**self.manifest, **change})
        with self.assertRaises(ValueError):
            self.binding(manifest={**self.manifest, "secret": "do-not-allow"})
        with self.assertRaises(ValueError):
            self.binding(manifest={k:v for k,v in self.manifest.items()
                                   if k != "control_run_id"})

    def test_digest_and_trusted_pins_cannot_be_swapped(self):
        for kw in (
            {"digest": "0"*64}, {"digest": "invalid"},
            {"sha": "a"*40}, {"reviewer": REVIEWER+1},
            {"environment": ENV+1}, {"reviewer": True},
        ):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.binding(**kw)
        altered = {**self.manifest, "task_id": "task-beta"}
        with self.assertRaises(ValueError):
            self.binding(manifest=altered, digest=hashlib.sha256(
                canonical(altered).encode()).hexdigest())

    def test_review_cannot_be_claimed_from_forged_or_stale_probe(self):
        for change in (
            {"matching_approval_record_observed": False},
            {"required_reviewer_configuration_observed": False},
            {"reviewer_id": REVIEWER+1},
            {"environment_id": ENV+1},
            {"triggering_actor_id": REVIEWER},
            {"control_sha": "f"*40},
            {"control_run_id": "999999"},
            {"cancel_authorized": True},
            {"target_specific_approval_verified": True},
            {"approval_freshness_verified": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.binding(probe={**self.probe, **change})

    def test_missing_wrong_receipt_or_expiry_refused(self):
        for evidence in (
            {**RECEIPT, "task_id": "other"},
            {**RECEIPT, "head_sha": "c"*40},
            {**RECEIPT, "attempt": "2"},
            {**RECEIPT, "multica_issue": "OAS-999"},
        ):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.binding(receipt=evidence)
        with self.assertRaises(ValueError):
            self.binding(now=NOW + timedelta(minutes=5))
        with self.assertRaises(ValueError):
            self.binding(now=datetime(2026, 10, 23, 19, 0))

    def test_control_reviewer_evidence_alone_cannot_grant_permission(self):
        self.assertTrue(self.probe["matching_approval_record_observed"])
        self.assertIs(self.probe["cancel_authorized"], False)
        self.assertIs(self.binding()["cancel_authorized"], False)


if __name__ == "__main__":
    unittest.main()
