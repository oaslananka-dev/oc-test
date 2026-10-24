#!/usr/bin/env python3
"""Explicit, human-reviewed cancellation of a superseded external Fleet PR.

A cancelled original worker PR is NEVER converted to "done". The replacement
is a separately merged PR and is not retroactively attributed to that worker.
Default mode is strictly read-only; never called by dispatch or reconciliation
timers. The human must verify semantic equivalence outside this tool.
"""
import argparse
import json
import re
import subprocess

import oc_fleet_external_base_proof as base_proof
import oc_fleet_external_finalize as finalizer

NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")


def require(ok, why):
    finalizer.require(ok, why)


def api(path):
    return finalizer.api(path)


def changed_files(repo, number):
    files = api(f"repos/{repo}/pulls/{number}/files?per_page=100")
    require(type(files) is list and 0 < len(files) < 100
            and all(type(x) is dict
                    and type(x.get("filename")) is str
                    and x.get("status") in ("added", "modified")
                    for x in files), "Missing or incomplete PR file inventory")
    names = [x["filename"] for x in files]
    require(len(names) == len({p.casefold() for p in names}),
            "Duplicate or ambiguous PR paths")
    return names


def covered(path, scope):
    return (path.casefold() == scope.casefold()
            or (scope.endswith("/") and path.casefold().startswith(scope.casefold())))


def original_closed_pr(m, paths, source_sha, allow_advance):
    repo, number = m["fleet_target_repo"], m["fleet_pull_request"]
    pr = api(f"repos/{repo}/pulls/{number}")
    require(type(pr) is dict
            and pr.get("number") == int(number)
            and pr.get("state") == "closed"
            and pr.get("merged") is False
            and pr.get("merged_at") is None
            and pr.get("merge_commit_sha") is None
            and type(pr.get("closed_at")) is str
            and (pr.get("head") or {}).get("repo", {}).get("full_name", "").lower()
                == repo.lower()
            and (pr.get("base") or {}).get("repo", {}).get("full_name", "").lower()
                == repo.lower()
            and (pr.get("base") or {}).get("ref") == "main",
            "Original worker PR was not independently closed/unmerged")
    require(re.search(r"(?i)\bcloses\s+#"
                      + re.escape(m["fleet_target_issue"]) + r"\b",
                      pr.get("body") or ""),
            "Original PR does not bind the expected issue")
    head = (pr.get("head") or {}).get("sha")
    require(finalizer.exact_sha(head), "Bad original head")
    commits = api(f"repos/{repo}/pulls/{number}/commits?per_page=100")
    require(type(commits) is list and len(commits) == 1
            and commits[0].get("sha") == head,
            "Original PR head/commits do not match worker proof")
    commit = api(f"repos/{repo}/git/commits/{head}")
    parents = commit.get("parents") if type(commit) is dict else None
    require(type(parents) is list and len(parents) == 1
            and finalizer.exact_sha(parents[0].get("sha")),
            "Original source commit is ambiguous")
    parent = parents[0]["sha"]
    if parent != source_sha:
        require(allow_advance is True,
                "Original worker base advanced without authorization")
        base_proof.validate(repo, source_sha, parent, paths, api)
    changed = changed_files(repo, number)
    require(all(any(covered(path, scope) for scope in paths) for path in changed),
            "Original PR changed files outside the worker's scope")
    return changed


def replacement_merge(m, number, merge_sha, original_paths):
    repo = m["fleet_target_repo"]
    require(type(number) is str and NUMBER.fullmatch(number)
            and number != m["fleet_pull_request"],
            "Replacement must be a distinct exact PR")
    require(finalizer.exact_sha(merge_sha), "Invalid replacement merge SHA")
    pr = api(f"repos/{repo}/pulls/{number}")
    require(type(pr) is dict and pr.get("number") == int(number)
            and pr.get("state") == "closed"
            and pr.get("merged") is True
            and type(pr.get("merged_at")) is str and pr.get("merged_at")
            and pr.get("merge_commit_sha") == merge_sha
            and (pr.get("head") or {}).get("repo", {}).get("full_name", "").lower()
                == repo.lower()
            and (pr.get("base") or {}).get("repo", {}).get("full_name", "").lower()
                == repo.lower()
            and (pr.get("base") or {}).get("ref") == "main",
            "Replacement PR merge or identity not verified")
    head = (pr.get("head") or {}).get("sha")
    require(finalizer.exact_sha(head), "Invalid replacement head SHA")
    # Human semantic review is still required. This merely proves the
    # replacement actually touched all files that the original PR changed.
    names = {p.casefold() for p in changed_files(repo, number)}
    require({p.casefold() for p in original_paths} <= names,
            "Replacement does not cover the original PR paths")
    issue_number = int(m["fleet_target_issue"])
    issue = api(f"repos/{repo}/issues/{issue_number}")
    require(issue.get("number") == issue_number
            and issue.get("state") == "closed"
            and issue.get("state_reason") == "completed",
            "Target GitHub issue is not independently completed")
    # Check the replacement head, rather than only a merged flag. A previous
    # human may have bypassed effective required branch protection rules.
    checks = finalizer.required_checks(repo, head)
    ref = api(f"repos/{repo}/git/ref/heads/main")
    main_sha = (ref.get("object") or {}).get("sha") if type(ref) is dict else None
    require(finalizer.exact_sha(main_sha), "Current protected main unknown")
    if main_sha != merge_sha:
        compare = api(f"repos/{repo}/compare/{merge_sha}...{main_sha}")
        require(type(compare) is dict
                and compare.get("status") == "ahead"
                and compare.get("behind_by") == 0
                and type(compare.get("ahead_by")) is int
                and 1 <= compare["ahead_by"] <= 250
                and (compare.get("merge_base_commit") or {}).get("sha") == merge_sha,
                "Replacement merge is not a bounded ancestor of current main")
    return {"replacement_pr": int(number), "replacement_merge_sha": merge_sha,
            "verified_checks": checks}


def review(issue_key, project, replacement_pr, merge_sha):
    issue, m, paths = finalizer.get_task(
        issue_key, project, allowed_statuses=("in_review", "cancelled"))
    require(m.get("fleet_result") in ("pr_created", "superseded"),
            "Unexpected Fleet completion state")
    require(m.get("fleet_merge_sha") is None,
            "Original worker merge SHA must never be present")
    manifest = finalizer.original_receipt(m, issue_key)
    finalizer.worker_success(m)
    changed = original_closed_pr(
        m, paths, manifest["checkout_sha"],
        m.get("fleet_allow_safe_base_advance") is True)
    result = replacement_merge(m, replacement_pr, merge_sha, changed)
    if issue["status"] == "cancelled":
        require(m.get("fleet_result") == "superseded"
                and m.get("fleet_superseded_by_pr") == replacement_pr
                and m.get("fleet_superseded_merge_sha") == merge_sha,
                "Cancelled Fleet task has conflicting proof")
        state = "already_superseded"
    else:
        require(issue["status"] == "in_review"
                and m.get("fleet_result") == "pr_created"
                and m.get("fleet_superseded_by_pr") is None
                and m.get("fleet_superseded_merge_sha") is None,
                "Supersession metadata is partial or conflicting")
        state = "ready_to_cancel"
    return {"state": state, "issue": issue_key,
            "original_pr": int(m["fleet_pull_request"]), **result}


def apply(result, issue_key, replacement_pr, merge_sha):
    require(result.get("state") == "ready_to_cancel"
            and result.get("issue") == issue_key
            and result.get("replacement_pr") == int(replacement_pr)
            and result.get("replacement_merge_sha") == merge_sha,
            "Mutation not authorized for these exact identifiers")
    # Never edit original run/PR IDs or fleet_merge_sha; never report done.
    finalizer.command("multica", "issue", "metadata", "set", issue_key,
                      "--key", "fleet_superseded_by_pr",
                      "--value", replacement_pr, "--type", "string")
    finalizer.command("multica", "issue", "metadata", "set", issue_key,
                      "--key", "fleet_superseded_merge_sha",
                      "--value", merge_sha, "--type", "string")
    finalizer.command("multica", "issue", "metadata", "set", issue_key,
                      "--key", "fleet_result", "--value", "superseded",
                      "--type", "string")
    finalizer.command("multica", "issue", "status", issue_key,
                      "cancelled", "--no-start")
    actual = finalizer.handoff.read_issue(issue_key)
    m = actual.get("metadata") or {}
    require(actual.get("identifier") == issue_key
            and actual.get("status") == "cancelled"
            and m.get("fleet_result") == "superseded"
            and m.get("fleet_superseded_by_pr") == replacement_pr
            and m.get("fleet_superseded_merge_sha") == merge_sha
            and m.get("fleet_merge_sha") is None
            and m.get("fleet_ready") is False
            and m.get("fleet_auto_merge") is False
            and m.get("fleet_external_pr_only") is True,
            "Post-write supersession readback failed; inspect partial metadata")
    return "cancelled"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--replacement-pr", required=True)
    parser.add_argument("--replacement-merge-sha", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-issue", default="")
    parser.add_argument("--confirm-replacement-pr", default="")
    parser.add_argument("--confirm-merge-sha", default="")
    parser.add_argument("--confirm-resolution-reviewed", action="store_true",
                        help="Human verified semantic replacement outside this tool")
    args = parser.parse_args()
    try:
        # Reject invalid mutation requests BEFORE any evidence read or write.
        if args.apply:
            require(args.confirm_issue == args.issue
                    and args.confirm_replacement_pr == args.replacement_pr
                    and args.confirm_merge_sha == args.replacement_merge_sha
                    and args.confirm_resolution_reviewed is True,
                    "Specific user-reviewed supersession confirmation missing")
        result = review(args.issue, args.project_id,
                        args.replacement_pr, args.replacement_merge_sha)
        if args.apply:
            require(result["state"] == "ready_to_cancel",
                    "Cancelled or unverified task must not be mutated")
            apply(result, args.confirm_issue, args.confirm_replacement_pr,
                  args.confirm_merge_sha)
            result["state"] = "applied"
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError,
            subprocess.SubprocessError, json.JSONDecodeError) as err:
        print(json.dumps({"state": "blocked", "reason": str(err)[:180]}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
