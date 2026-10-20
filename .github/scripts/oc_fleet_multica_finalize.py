#!/usr/bin/env python3
"""Opt-in GitHub PR CI/review completion for one fixed pilot repository.

No model is used. All inputs must be an owner-prepared Multica issue plus
the trusted local worker dispatch receipt; fail closed on missing evidence.
"""
import json
from pathlib import Path
import re
import subprocess

import oc_fleet_multica as handoff

PILOT_REPO = "oaslananka-dev/oc-test"
PILOT_REQUIRED = {"baseline", "semgrep-cloud-platform/scan"}


def run(*argv):
    result = subprocess.run(list(argv), text=True, capture_output=True,
                            check=True, timeout=30)
    return result.stdout.strip()


def gh(path):
    return json.loads(run("gh", "api", path))


def list_for_review(project):
    response = json.loads(run(
        "multica", "issue", "list", "--project", project,
        "--metadata", "fleet_result=pr_created", "--limit", "100",
        "--fields", "identifier,status,project_id,assignee_type,metadata",
        "--output", "json",
    ))
    if not isinstance(response, dict) or response.get("has_more") is not False:
        raise ValueError("Incomplete PR review listing")
    items = response.get("issues")
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Invalid PR review listing")
    return [i for i in items if i.get("status") == "in_review"]


def verify_receipt(meta, receipt):
    if not isinstance(receipt, dict):
        raise ValueError("Invalid controller receipt")
    manifest = receipt.get("manifest") or {}
    if (receipt.get("worker_repo") != meta["fleet_worker_repo"]
            or str(receipt.get("run_id")) != meta.get("fleet_run_id")
            or manifest.get("repository", "").lower() != PILOT_REPO
            or manifest.get("issue_key") != "GH-" + meta["fleet_target_issue"]
            or manifest.get("allowed_paths") != [meta["fleet_allowed_path"]]):
        raise ValueError("Dispatch receipt does not match Multica review")


def review_pr(meta):
    number = int(meta["fleet_pull_request"])
    issue = meta["fleet_target_issue"]
    repo = PILOT_REPO
    pr = gh(f"repos/{repo}/pulls/{number}")
    if (pr.get("number") != number or pr.get("base", {}).get("ref") != "main"
            or pr.get("base", {}).get("repo", {}).get("full_name") != repo
            or pr.get("head", {}).get("repo", {}).get("full_name") != repo):
        raise ValueError("Unexpected PR base, head or identity")
    if not re.search(r"(?i)\bcloses\s+#" + re.escape(issue) + r"\b", pr.get("body") or ""):
        raise ValueError("PR is not tied to the intended GitHub issue")
    files = gh(f"repos/{repo}/pulls/{number}/files?per_page=100")
    if (not isinstance(files, list) or len(files) != 1
            or files[0].get("filename") != meta["fleet_allowed_path"]
            or files[0].get("status") not in ("added", "modified")):
        raise ValueError("PR changes exceed approved file scope")
    return pr


def checks_ok(repo, number, sha):
    result = json.loads(run("gh", "pr", "view", str(number), "-R", repo,
                            "--json", "headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup"))
    if result.get("headRefOid") != sha or result.get("mergeStateStatus") != "CLEAN":
        return False
    if result.get("reviewDecision") in ("CHANGES_REQUESTED", "REVIEW_REQUIRED"):
        return False
    checks = result.get("statusCheckRollup") or []
    names = {c.get("name") for c in checks}
    if not PILOT_REQUIRED.issubset(names):
        return False
    for c in checks:
        if c.get("status") != "COMPLETED":
            return False
        if c.get("name") in PILOT_REQUIRED:
            if c.get("conclusion") != "SUCCESS":
                return False
        elif c.get("conclusion") not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            return False
    return True


def comments_clear(repo, number):
    reviews = gh(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    if not isinstance(reviews, list) or len(reviews) >= 100:
        return False
    if any(r.get("state") in ("CHANGES_REQUESTED", "PENDING") for r in reviews):
        return False
    inline = gh(f"repos/{repo}/pulls/{number}/comments?per_page=100")
    discussion = gh(f"repos/{repo}/issues/{number}/comments?per_page=100")
    return isinstance(inline, list) and isinstance(discussion, list) and not inline and not discussion


def finalize_one(item, project, execute, receipt_path):
    key = item.get("identifier")
    if not isinstance(key, str) or not handoff.ISSUE_RE.fullmatch(key):
        raise ValueError("Invalid review issue identifier")
    full = handoff.read_issue(key)
    m = full.get("metadata") or {}
    if (full.get("project_id") != project or full.get("status") != "in_review"
            or full.get("assignee_type") == "agent"
            or m.get("fleet_target_repo") != PILOT_REPO
            or m.get("fleet_auto_merge") is not True
            or m.get("fleet_ready") is not False
            or m.get("fleet_result") != "pr_created"
            or not isinstance(m.get("fleet_target_issue"), str)
            or not re.fullmatch(r"[1-9][0-9]{0,8}", m["fleet_target_issue"])
            or not isinstance(m.get("fleet_pull_request"), str)
            or not re.fullmatch(r"[1-9][0-9]{0,8}", m["fleet_pull_request"])
            or not isinstance(m.get("fleet_run_id"), str)):
        return "not_authorized"
    if not isinstance(m.get("fleet_allowed_path"), str) or not m["fleet_allowed_path"].startswith("playground/fleet/"):
        raise ValueError("PR file scope not approved")
    filename = receipt_path(m)
    if not filename.is_file():
        raise ValueError("No trusted dispatch receipt for PR finalization")
    verify_receipt(m, json.loads(filename.read_text()))
    pr = review_pr(m)
    repo = PILOT_REPO
    number = int(m["fleet_pull_request"])
    merged = bool(pr.get("merged"))
    if not merged:
        if pr.get("state") != "open" or pr.get("draft"):
            return "blocked"
        sha = pr.get("head", {}).get("sha")
        if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{40}", sha):
            raise ValueError("Invalid PR head")
        if not checks_ok(repo, number, sha) or not comments_clear(repo, number):
            return "waiting_ci_or_review"
        if not execute:
            print("READY_TO_MERGE",key,number)
            return "planned"
        run("gh", "pr", "merge", str(number), "-R", repo, "--squash",
            "--match-head-commit", sha)
        pr = review_pr(m)
        merged = bool(pr.get("merged"))
    if not merged:
        return "merge_pending"
    github_issue = gh(f"repos/{repo}/issues/{m['fleet_target_issue']}")
    if github_issue.get("state") != "closed":
        return "waiting_issue_closure"
    file_data = gh(f"repos/{repo}/contents/{m['fleet_allowed_path']}?ref=main")
    if file_data.get("type") != "file":
        raise ValueError("Merged target file not found on main")
    if not execute:
        print("READY_TO_COMPLETE",key,number)
        return "planned"
    merge_sha = pr.get("merge_commit_sha")
    if not isinstance(merge_sha, str) or not re.fullmatch("[a-f0-9]{40}", merge_sha):
        raise ValueError("Merge commit missing")
    for args in (
        ["metadata","set",key,"--key","fleet_result","--value","completed","--type","string"],
        ["metadata","set",key,"--key","fleet_merge_sha","--value",merge_sha,"--type","string"],
        ["status",key,"done","--no-start"],
    ):
        run("multica", "issue", *args)
    print("FLEET_DONE", key, number)
    return "completed"


def poll(project, execute, receipt_path):
    items = sorted(list_for_review(project), key=lambda x:x.get("identifier",""))
    print("IN_REVIEW",len(items))
    for i in items:
        outcome = finalize_one(i,project,execute,receipt_path)
        print("REVIEW",i["identifier"],outcome)
        if outcome not in ("not_authorized","waiting_ci_or_review","blocked"):
            return outcome
    return "empty_or_waiting"
