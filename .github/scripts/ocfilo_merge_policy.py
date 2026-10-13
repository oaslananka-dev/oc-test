#!/usr/bin/env python3
"""Fail-closed, read-only F1 merge-policy assurance.

Technical readiness is not policy enforcement. A GitHub-hosted observer without
administration/read permission must report unavailable detailed policy evidence,
never infer it from 'protected: true'. An authorized local manager may supply
the exact protection GET response through --protection.
"""
import argparse
import json
import re
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
SHA = re.compile(r"[0-9a-f]{40}\Z")
CHECK = "baseline"
CHECK_APP_ID = 15368


def _enabled(protection, name, expected):
    field = protection.get(name)
    return (isinstance(field, dict)
            and type(field.get("enabled")) is bool
            and field["enabled"] is expected)


def _matches_approved_f1(protection):
    """Exact approved policy, not a generic determination of GH mergeability."""
    if not isinstance(protection, dict):
        return False
    checks = protection.get("required_status_checks")
    review = protection.get("required_pull_request_reviews")
    if not isinstance(checks, dict) or not isinstance(review, dict):
        return False
    required = checks.get("checks")
    if not isinstance(required, list) or len(required) != 1:
        return False
    check = required[0]
    return all([
        isinstance(check, dict),
        check.get("context") == CHECK if isinstance(check, dict) else False,
        type(check.get("app_id")) is int and check["app_id"] == CHECK_APP_ID
        if isinstance(check, dict) else False,
        checks.get("strict") is False,
        type(review.get("required_approving_review_count")) is int,
        review.get("required_approving_review_count") == 0,
        _enabled(protection, "enforce_admins", True),
        _enabled(protection, "allow_force_pushes", False),
        _enabled(protection, "allow_deletions", False),
        _enabled(protection, "lock_branch", False),
    ])


def audit(pr, branch, rulesets, technical, protection=None):
    if not all(isinstance(v, dict) for v in (pr, branch, technical)):
        raise ValueError("Missing repository/PR/policy evidence")
    if not isinstance(rulesets, list):
        raise ValueError("Incomplete ruleset listing")
    if (type(pr.get("number")) is not int or pr["number"] not in (1, 2)
        or pr.get("base", {}).get("ref") != "main"
        or pr.get("base", {}).get("repo", {}).get("full_name", REPO) != REPO
        or pr.get("head", {}).get("repo", {}).get("full_name") != REPO):
        raise ValueError("Outside ocfilo F1 test PR scope")
    sha = pr.get("head", {}).get("sha")
    if (not isinstance(sha, str) or not SHA.fullmatch(sha)
        or technical.get("repository") != REPO
        or technical.get("pr_number") != pr["number"]
        or technical.get("head_sha") != sha):
        raise ValueError("Live PR SHA and F1 technical decision mismatch")
    if branch.get("name") != "main" or type(branch.get("protected")) is not bool:
        raise ValueError("Cannot verify default branch protection")
    if not all(isinstance(r, dict) and "enforcement" in r for r in rulesets):
        raise ValueError("Invalid or incomplete repository rulesets")
    active_rules = sum(r.get("enforcement") == "active" for r in rulesets)
    policy_config_matches = (
        branch["protected"] is True and active_rules == 0
        and _matches_approved_f1(protection)
    )
    if pr.get("state") != "open" or pr.get("merged") is True:
        status = "CLOSED"
    elif technical.get("state") != "READY_FOR_MERGE" or technical.get("dispatch_allowed") is not False:
        status = "F1_CHECKS_NOT_READY"
    elif branch["protected"] is False and active_rules == 0:
        status = "F1_CHECKS_PASS_BUT_NO_ENFORCED_GITHUB_POLICY"
    elif active_rules:
        status = "ADDITIONAL_ACTIVE_RULESETS_REQUIRE_REVIEW"
    elif protection is None:
        # GitHub Actions GITHUB_TOKEN usually cannot read this admin-gated API.
        status = "GITHUB_POLICY_DETAILS_NEED_VERIFICATION"
    elif not policy_config_matches:
        status = "GITHUB_POLICY_CONFIGURATION_MISMATCH"
    else:
        status = "GITHUB_POLICY_VERIFIED_FOR_F1"
    return {
        "repository": REPO, "pr_number": pr["number"], "head_sha": sha,
        "f1_decision": technical.get("state"),
        "base_branch_protected": branch["protected"],
        "active_repository_rulesets": active_rules,
        "required_check": CHECK,
        "required_check_app_id": CHECK_APP_ID,
        "protection_details_supplied": protection is not None,
        "approved_protection_configuration_verified": policy_config_matches,
        "policy_status": status,
        "github_policy_verified": status == "GITHUB_POLICY_VERIFIED_FOR_F1",
        "automatic_merge_allowed": False,
        "action": "REPORT_ONLY",
    }


def main():
    parser = argparse.ArgumentParser()
    for label in ("pr", "branch", "rulesets", "technical", "output"):
        parser.add_argument("--" + label, required=True)
    parser.add_argument("--protection", help="Authorized local GET branch-protection evidence (optional)")
    args = parser.parse_args()

    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    result = audit(
        read(args.pr), read(args.branch), read(args.rulesets),
        read(args.technical), read(args.protection) if args.protection else None
    )
    Path(args.output).write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
