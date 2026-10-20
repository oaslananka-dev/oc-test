#!/usr/bin/env python3
"""Read-only, SHA-bound PR triage for a fixed pilot and one real Python repo.

Check names indicate *where* something failed, not its root cause. Neither
untrusted comment bodies nor check logs are executed or forwarded to workers.
This module never authorizes autonomous repair, merge or GitHub writes.
"""
import argparse
import json
import re
import subprocess

ALLOWED_REPOS = frozenset({"oaslananka-dev/oc-test", "oaslananka/adxl355"})
SHA = re.compile(r"[0-9a-f]{40}\Z")
FAILED = frozenset({"failure", "timed_out", "cancelled", "action_required", "startup_failure"})
GOOD = frozenset({"success", "neutral", "skipped"})
PENDING = frozenset({"queued", "in_progress", "pending", "requested", "waiting"})


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def api(repo, path):
    require(repo in ALLOWED_REPOS, "Repository not allowlisted")
    require(path.startswith("repos/" + repo + "/"), "Unexpected GitHub API route")
    p = subprocess.run(
        ["gh", "api", path, "-H", "Accept: application/vnd.github+json"],
        text=True, capture_output=True, timeout=25, check=True)
    return json.loads(p.stdout)


def list_limited(repo, path):
    obj = api(repo, path + "?per_page=100")
    require(isinstance(obj, list) and len(obj) < 100, "Missing or paginated review evidence")
    return obj


def acquire(repo, number, expected_sha=None):
    require(repo in ALLOWED_REPOS and type(number) is int and 1 <= number <= 1000000,
            "PR outside read-only pilot")
    pr = api(repo, f"repos/{repo}/pulls/{number}")
    require(type(pr) is dict and pr.get("number") == number
            and (pr.get("base") or {}).get("repo", {}).get("full_name") == repo,
            "PR base or identity mismatch")
    sha = (pr.get("head") or {}).get("sha")
    require(isinstance(sha, str) and SHA.fullmatch(sha), "Invalid head SHA")
    if expected_sha is not None:
        require(expected_sha == sha, "Requested SHA is stale")
    checks = api(repo, f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    require(type(checks) is dict and type(checks.get("total_count")) is int
            and isinstance(checks.get("check_runs"), list)
            and checks["total_count"] == len(checks["check_runs"])
            and len(checks["check_runs"]) < 100, "Missing or paginated check-run evidence")
    statuses = api(repo, f"repos/{repo}/commits/{sha}/status?per_page=100")
    require(type(statuses) is dict and isinstance(statuses.get("statuses"), list)
            and len(statuses["statuses"]) < 100, "Missing or paginated commit status evidence")
    reviews = list_limited(repo, f"repos/{repo}/pulls/{number}/reviews")
    inline = list_limited(repo, f"repos/{repo}/pulls/{number}/comments")
    issue = list_limited(repo, f"repos/{repo}/issues/{number}/comments")
    after = api(repo, f"repos/{repo}/pulls/{number}")
    require((after.get("head") or {}).get("sha") == sha
            and after.get("state") == pr.get("state"), "Stale PR snapshot: changed during reads")
    return dict(pr=pr, checks=checks, statuses=statuses,
                reviews=reviews, inline=inline, discussion=issue)


def check_family(name):
    """Signal classification only, never trust this for write authorization."""
    require(isinstance(name, str) and 0 < len(name) <= 240, "Invalid check context")
    s = name.casefold()
    if any(part in s for part in ("osv", "dependency audit", "dependabot", "security audit", "npm audit")):
        return "DEPENDENCY_SECURITY"
    if any(part in s for part in ("sonar", "codacy", "codecov", "coverage", "quality gate")):
        return "QUALITY_COVERAGE"
    if any(part in s for part in ("test", "pytest", "node.js", "build", "lint", "rust", "go ", "ci /")):
        return "TEST_BUILD"
    if any(part in s for part in ("semgrep", "codeql", "sast", "security scan")):
        return "SECURITY_STATIC"
    return "OTHER_CI"


def comment_provenance(comments, origin):
    require(origin in ("inline", "discussion"), "Unknown review comment source")
    groups = {"human_" + origin:0, "bot_" + origin:0, "unknown_" + origin:0}
    for c in comments:
        require(isinstance(c, dict), "Invalid review comment")
        author = c.get("user") or {}
        require(isinstance(author,dict), "Invalid review comment author")
        who = author.get("login")
        kind = author.get("type")
        if kind == "Bot" or (isinstance(who,str) and who.endswith("[bot]")):
            groups["bot_" + origin] += 1
        elif kind == "User":
            groups["human_" + origin] += 1
        else:
            groups["unknown_" + origin] += 1
    return groups


def triage(snapshot):
    require(isinstance(snapshot, dict), "Missing PR snapshot")
    pr, checks, statuses = snapshot["pr"], snapshot["checks"], snapshot["statuses"]
    number = pr.get("number")
    repo = (pr.get("base") or {}).get("repo", {}).get("full_name")
    sha = (pr.get("head") or {}).get("sha")
    require(repo in ALLOWED_REPOS and type(number) is int and number > 0
            and type(sha) is str and SHA.fullmatch(sha), "Invalid PR identity")
    require(type(checks.get("total_count")) is int
            and checks.get("total_count") == len(checks.get("check_runs", []))
            and len(checks["check_runs"]) < 100, "Incomplete checks")
    require(isinstance(statuses.get("statuses"), list) and len(statuses["statuses"]) < 100,
            "Incomplete statuses")
    result = {"repository":repo, "pr_number":number,"head_sha":sha,
              "state":"NEEDS_TRIAGE","action":"MANUAL_REVIEW",
              "dispatch_allowed":False,"failing_checks":[],"pending_checks":[],
              "check_families":[], "review_comment_count":0,
              "blocking_reviewer_count":0, "review_comment_sources":{},
              "review_pending":False, "base_behind":False, "has_conflicts":False,
              "merge_state":pr.get("mergeable_state")}
    if pr.get("state") != "open" or pr.get("merged") is True:
        result.update(state="CLOSED",action="NO_ACTION")
        return result
    pending, failed, families = set(), set(), set()
    grouped = {}
    for c in checks["check_runs"]:
        require(isinstance(c, dict), "Invalid check")
        if c.get("head_sha") != sha:
            continue
        name = c.get("name")
        require(isinstance(name,str) and name, "Unknown check name")
        grouped.setdefault(name, []).append(c)
    for name, runs in grouped.items():
        ids = [c.get("id") for c in runs]
        if len(runs) > 1 and len(set(ids)) == len(ids) and all(type(i) is int and i > 0 for i in ids):
            runs = [max(runs, key=lambda c: c["id"])]
        for c in runs:
            if c.get("status") != "completed":
                pending.add(name)
            elif c.get("conclusion") in FAILED:
                failed.add(name);families.add(check_family(name))
            elif c.get("conclusion") not in GOOD:
                failed.add(name);families.add("OTHER_CI")
    contexts = {}
    for c in statuses["statuses"]:
        require(isinstance(c, dict), "Invalid status")
        if c.get("sha",sha) != sha:
            continue
        name = c.get("context")
        require(isinstance(name,str) and name, "Invalid status context")
        contexts.setdefault(name,[]).append(c)
    for name, rows in contexts.items():
        if len(rows) > 1:
            timestamps = [r.get("created_at") for r in rows]
            if all(isinstance(t,str) and t for t in timestamps) and len(set(timestamps)) == len(timestamps):
                rows=[max(rows,key=lambda r:r["created_at"])]
        for c in rows:
            state = c.get("state")
            key = "status:" + name
            if state == "pending":
                pending.add(key)
            elif state != "success":
                failed.add(key);families.add(check_family(name))
    require(all(isinstance(snapshot.get(k),list) and len(snapshot[k]) < 100
                for k in ("reviews","inline","discussion")), "Incomplete review or comment evidence")
    latest={}
    for review in snapshot["reviews"]:
        require(isinstance(review,dict), "Bad review")
        who=(review.get("user") or {}).get("login")
        if isinstance(who,str) and who:
            if review.get("state") in ("APPROVED","CHANGES_REQUESTED","DISMISSED"):
                latest[who]=review["state"]
    blocked = sum(x=="CHANGES_REQUESTED" for x in latest.values())
    comment_count = len(snapshot["inline"]) + len(snapshot["discussion"])
    comments = {**comment_provenance(snapshot["inline"],"inline"),
                **comment_provenance(snapshot["discussion"],"discussion")}
    review_pending = bool(pr.get("requested_reviewers") or pr.get("requested_teams"))
    merge_state = pr.get("mergeable_state")
    result.update(failing_checks=sorted(failed),pending_checks=sorted(pending),
                  check_families=sorted(families),review_comment_count=comment_count,
                  review_comment_sources=comments,
                  review_pending=review_pending,blocking_reviewer_count=blocked,
                  base_behind=(merge_state == "behind"),
                  has_conflicts=(merge_state == "dirty" or pr.get("mergeable") is False))
    # Check names and statuses are evidence of execution, not proof that a
    # repository's full mandatory check list is present.
    if not grouped and not contexts:
        state,action="INSUFFICIENT_EVIDENCE","MANUAL_REVIEW"
    elif pending:
        state,action="WAITING_CI","WAIT_FOR_CHECKS"
    elif failed:
        state,action="FAILED_CI","INSPECT_CHECK_LOGS"
    elif blocked or comment_count or review_pending:
        state,action="REVIEW_TRIAGE","CLASSIFY_FEEDBACK"
    elif pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        state,action="MERGE_CONFLICT","RESOLVE_CONFLICT_MANUALLY"
    elif pr.get("mergeable_state") == "behind":
        state,action="BASE_BEHIND","ASSESS_BASE_UPDATE"
    elif pr.get("mergeable") is True and pr.get("mergeable_state") == "clean":
        state,action="OBSERVED_GREEN","NO_ACTION"
    else:
        state,action="UNKNOWN_MERGE_STATE","MANUAL_REVIEW"
    result.update(state=state,action=action)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, choices=sorted(ALLOWED_REPOS))
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--expected-sha", default=None)
    args = p.parse_args()
    print(json.dumps(triage(acquire(args.repo,args.pr,args.expected_sha)),
                     sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
