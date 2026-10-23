#!/usr/bin/env python3
"""OFFLINE comparison of a worker-bound review manifest against pinned evidence.

This is not an approval verifier or live action. The caller MUST obtain the
manifest digest, task receipt and reviewer pins from *independent authenticated*
sources in a future separately reviewed control plane. No such source is wired
here; caller-supplied values can all be synthetic or forged. No CLI or POST.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re

from oc_fleet_cancel_admission import (
    canonical, matching_fields, timestamp, validate_proposal)
from oc_fleet_approval_probe import ENVIRONMENT
from oc_fleet_supervisor import REPO, WORKFLOW, SHA

DIGEST = re.compile(r"[0-9a-f]{64}\Z")
CONTROL_RUN = re.compile(r"[1-9][0-9]{0,19}\Z")
MANIFEST_FIELDS = frozenset((
    "schema_version", "action", "proposal_id", "repository", "workflow",
    "run_id", "run_attempt", "head_sha", "registered_multica_issue",
    "task_id", "control_run_id", "control_head_sha", "environment_id",
    "reviewer_id", "requester_id", "issued_at_utc", "expires_at_utc",
    "executable",
))


def inspect_binding(proposal, receipt, manifest, control_probe, *,
                    pinned_manifest_digest, pinned_control_sha,
                    pinned_reviewer_id, pinned_environment_id, now):
    """Match offline evidence; always return a non-authorizing receipt.

    Positive output means only that the independently *claimed* evidence
    matches byte-for-byte, not that any input has been authenticated.
    """
    if (not isinstance(now, datetime) or now.tzinfo is None
            or not isinstance(manifest, dict)
            or set(manifest) != MANIFEST_FIELDS
            or not isinstance(pinned_manifest_digest, str)
            or not DIGEST.fullmatch(pinned_manifest_digest)
            or not isinstance(pinned_control_sha, str)
            or not SHA.fullmatch(pinned_control_sha)
            or type(pinned_reviewer_id) is not int or pinned_reviewer_id <= 0
            or type(pinned_environment_id) is not int
            or pinned_environment_id <= 0):
        raise ValueError("invalid or unpinned control evidence")

    fields = validate_proposal(proposal, now)
    task = matching_fields(fields, receipt)
    if (type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1
            or manifest["action"] != "review_cancel"
            or manifest["executable"] is not False):
        raise ValueError("invalid non-executable manifest")
    expected = {
        "proposal_id": proposal["proposal_id"],
        "repository": REPO, "workflow": WORKFLOW,
        "run_id": fields["run_id"],
        "run_attempt": fields["run_attempt"],
        "head_sha": fields["head_sha"],
        "registered_multica_issue": fields["registered_multica_issue"],
        "task_id": task,
        "control_head_sha": pinned_control_sha,
        "environment_id": pinned_environment_id,
        "reviewer_id": pinned_reviewer_id,
    }
    for key, value in expected.items():
        actual = manifest.get(key)
        if type(actual) is not type(value) or actual != value:
            raise ValueError("target/control manifest identity mismatch")
    run = manifest.get("control_run_id")
    requester = manifest.get("requester_id")
    if (not isinstance(run, str) or not CONTROL_RUN.fullmatch(run)
            or type(requester) is not int or requester <= 0
            or requester == pinned_reviewer_id):
        raise ValueError("invalid control actor/run")
    issued = timestamp(manifest["issued_at_utc"])
    deadline = timestamp(manifest["expires_at_utc"])
    request_time = timestamp(proposal["requested_at_utc"])
    proposal_deadline = timestamp(proposal["expires_at_utc"])
    utc_now = now.astimezone(timezone.utc)
    if (issued < request_time or issued > utc_now + timedelta(seconds=15)
            or deadline <= utc_now or deadline <= issued
            or deadline > proposal_deadline
            or deadline - issued > timedelta(minutes=5)):
        raise ValueError("unbound, future, or expired review deadline")

    try:
        digest = hashlib.sha256(
            canonical(manifest).encode("utf-8")).hexdigest()
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid manifest JSON") from None
    if not hmac.compare_digest(digest, pinned_manifest_digest):
        raise ValueError("manifest differs from independent digest")

    if not isinstance(control_probe, dict):
        raise ValueError("missing review history")
    required = {
        "source": "github_native_review_history_get_only",
        "control_run_id": run,
        "control_sha": pinned_control_sha,
        "environment": ENVIRONMENT,
        "environment_id": pinned_environment_id,
        "reviewer_id": pinned_reviewer_id,
        "triggering_actor_id": requester,
        "matching_approval_record_observed": True,
        "required_reviewer_configuration_observed": True,
        "cancel_authorized": False,
        "can_cancel_workflow": False,
        "target_run_manifest_bound": False,
        "target_specific_approval_verified": False,
        "approval_freshness_verified": False,
        "environment_protection_verified": False,
    }
    for key, value in required.items():
        actual = control_probe.get(key)
        if type(actual) is not type(value) or actual != value:
            raise ValueError("review history/policy does not match")
    return {
        "schema_version": 1,
        "proposal_id": proposal["proposal_id"],
        "control_run_id": run,
        "target_binding_matches_pinned_inputs": True,
        "review_history_matches_pinned_inputs": True,
        "trusted_manifest_origin_verified": False,
        "production_identity_authenticated": False,
        "target_specific_approval_verified": False,
        "approval_freshness_verified": False,
        "execution_enabled": False,
        "cancel_authorized": False,
        "cancel_requested": False,
        "simulation_only": True,
    }
