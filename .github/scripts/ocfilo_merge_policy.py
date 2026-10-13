#!/usr/bin/env python3
"""Read-only, fail-closed F1 merge-policy assurance.

'READY_FOR_MERGE' in the deterministic PR engine means F1 technical checks
passed. It does NOT establish that GitHub branch policies are enforced.
This separate audit explicitly distinguishes those two things.
"""
import argparse
import json
import re
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
SHA = re.compile(r"[0-9a-f]{40}\Z")

def audit(pr, branch, rulesets, technical):
    if not all(isinstance(v, dict) for v in (pr, branch, technical)):
        raise ValueError("Missing repository/PR/policy evidence")
    if not isinstance(rulesets, list):
        raise ValueError("Incomplete ruleset listing")
    if (type(pr.get("number")) is not int or pr["number"] not in (1, 2)
        or pr.get("base", {}).get("ref") != "main"
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
    if pr.get("state") != "open" or pr.get("merged") is True:
        status = "CLOSED"
    elif technical.get("state") != "READY_FOR_MERGE" or technical.get("dispatch_allowed") is not False:
        status = "F1_CHECKS_NOT_READY"
    elif branch["protected"] is False and not any(r.get("enforcement") == "active" for r in rulesets):
        status = "F1_CHECKS_PASS_BUT_NO_ENFORCED_GITHUB_POLICY"
    else:
        # Rules/approvals/check requirements must be reviewed in detail.
        # A protection flag alone is not proof of *which* rules were enforced.
        status = "GITHUB_POLICY_DETAILS_NEED_VERIFICATION"
    return {
        "repository": REPO, "pr_number": pr["number"], "head_sha": sha,
        "f1_decision": technical.get("state"),
        "base_branch_protected": branch["protected"],
        "active_repository_rulesets": sum(r.get("enforcement") == "active" for r in rulesets),
        "policy_status": status,
        "github_policy_verified": False,
        "automatic_merge_allowed": False,
        "action": "REPORT_ONLY",
    }

def main():
    parser = argparse.ArgumentParser()
    for label in ("pr","branch","rulesets","technical","output"):
        parser.add_argument("--"+label, required=True)
    args = parser.parse_args()
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    result = audit(read(args.pr), read(args.branch), read(args.rulesets),
                   read(args.technical))
    Path(args.output).write_text(json.dumps(result, indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
