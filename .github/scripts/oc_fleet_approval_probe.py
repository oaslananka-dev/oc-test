#!/usr/bin/env python3
"""GET-only native GitHub review evidence; NEVER an execution authorization."""
import argparse
import json
import re
import subprocess

REPO = "oaslananka-dev/oc-test"
WORKFLOW = ".github/workflows/oc-fleet-cancel-control.yml"
ENVIRONMENT = "oc-fleet-cancel-review"
RUN = re.compile(r"[1-9][0-9]{0,19}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def read_api(path):
    result = subprocess.run(["gh", "api", path], check=True, text=True,
                            capture_output=True, timeout=15)
    return json.loads(result.stdout)


def required_reviewer_configuration(environment, reviewer_id, environment_id,
                                    triggering_id):
    """GET-only evidence about configured reviewers; never cancel authority."""
    if (not isinstance(environment, dict)
            or type(environment.get("id")) is not int
            or environment["id"] != environment_id
            or environment.get("name") != ENVIRONMENT
            or type(triggering_id) is not int or triggering_id <= 0
            or triggering_id == reviewer_id):
        raise ValueError("untrusted environment/trigger identity")
    rules = environment.get("protection_rules")
    if not isinstance(rules, list) or len(rules) > 20:
        raise ValueError("missing or unbounded environment rules")
    required = [r for r in rules if isinstance(r, dict)
                and r.get("type") == "required_reviewers"]
    if len(required) != 1 or required[0].get("prevent_self_review") is not True:
        raise ValueError("narrow human review policy not observed")
    reviewers = required[0].get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 1:
        raise ValueError("ambiguous permitted reviewers")
    entry = reviewers[0]
    if not isinstance(entry, dict) or entry.get("type") != "User":
        raise ValueError("human reviewer missing")
    user = entry.get("reviewer")
    if (not isinstance(user, dict) or type(user.get("id")) is not int
            or user["id"] != reviewer_id):
        raise ValueError("expected reviewer not configured")
    return True


def inspect(control_id, expected_sha, reviewer_id, environment_id, reader=read_api):
    """All expected IDs MUST come from a trusted policy, never a worker or model.

    Approval evidence is NOT worker-bound or permission to cancel. The named
    future control workflow/environment are NOT provisioned by this module.
    """
    if not isinstance(control_id, str) or not RUN.fullmatch(control_id):
        raise ValueError("invalid control run")
    if not isinstance(expected_sha, str) or not SHA.fullmatch(expected_sha):
        raise ValueError("untrusted control SHA")
    if (type(reviewer_id) is not int or reviewer_id <= 0
            or type(environment_id) is not int or environment_id <= 0
            or not callable(reader)):
        raise ValueError("trusted reviewer/environment or API missing")
    prefix = f"repos/{REPO}/actions/runs/{control_id}"
    run = reader(prefix)
    if not isinstance(run, dict):
        raise ValueError("invalid control response")
    repo = run.get("repository")
    if (type(run.get("id")) is not int or run["id"] != int(control_id)
            or type(run.get("run_attempt")) is not int or run["run_attempt"] != 1
            or not isinstance(repo, dict) or repo.get("full_name") != REPO
            or run.get("head_sha") != expected_sha
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or run.get("path") not in (WORKFLOW, WORKFLOW + "@main",
                                      WORKFLOW + "@refs/heads/main")):
        raise ValueError("untrusted control workflow identity")
    trigger = run.get("triggering_actor")
    if (not isinstance(trigger, dict) or trigger.get("type") != "User"
            or type(trigger.get("id")) is not int):
        raise ValueError("untrusted initiating actor")
    environment = reader(f"repos/{REPO}/environments/{ENVIRONMENT}")
    required_reviewer_configuration(environment, reviewer_id, environment_id,
                                    trigger["id"])
    reviews = reader(prefix + "/approvals")
    if not isinstance(reviews, list) or len(reviews) > 100:
        raise ValueError("unbounded/invalid review evidence")
    found = False
    for entry in reviews:
        if not isinstance(entry, dict):
            raise ValueError("invalid review entry")
        envs = entry.get("environments")
        if not isinstance(envs, list) or len(envs) > 20:
            raise ValueError("invalid environment list")
        same = any(isinstance(env, dict)
                   and type(env.get("id")) is int
                   and env["id"] == environment_id
                   and env.get("name") == ENVIRONMENT
                   for env in envs)
        if not same:
            continue
        if entry.get("state") == "rejected":
            raise ValueError("rejected environment gate")
        actor = entry.get("user")
        if (entry.get("state") == "approved" and isinstance(actor, dict)
                and actor.get("type") == "User"
                and type(actor.get("id")) is int
                and actor["id"] == reviewer_id):
            found = True
    return {
        "schema_version": 1,
        "source": "github_native_review_history_get_only",
        "control_run_id": control_id, "control_sha": expected_sha,
        "environment": ENVIRONMENT,
        "matching_approval_record_observed": found,
        "required_reviewer_configuration_observed": True,
        "environment_protection_verified": False,
        "target_run_manifest_bound": False,
        "target_specific_approval_verified": False,
        "approval_freshness_verified": False,
        "cancel_authorized": False, "can_cancel_workflow": False,
        "reason": "Approval history is evidence, not a target-specific cancel grant."
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Read native GitHub review history only")
    p.add_argument("--control-run", required=True)
    p.add_argument("--pinned-control-sha", required=True)
    p.add_argument("--reviewer-id", required=True, type=int)
    p.add_argument("--environment-id", required=True, type=int)
    a = p.parse_args(argv)
    print(json.dumps(inspect(a.control_run, a.pinned_control_sha,
                             a.reviewer_id, a.environment_id),
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.SubprocessError,
            json.JSONDecodeError, OSError) as exc:
        raise SystemExit("APPROVAL_PROBE_BLOCKED: " + type(exc).__name__) from None
