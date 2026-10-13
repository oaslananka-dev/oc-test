#!/usr/bin/env python3
"""Read-only F1 GitHub PR event observer.

Produces evidence from the current PR HEAD, but never dispatches, comments,
publishes, merges or executes bot instructions. Uses the existing state engine.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ocfilo_pr_state import decide

REPO = "oaslananka-dev/oc-test"
ALLOWED_PRS = (1, 2)
ROOT = "https://api.github.com"
PAGES_MAX = 12

def get(path):
    url = ROOT + path
    if not url.startswith(ROOT + "/repos/" + REPO + "/"):
        raise ValueError("Non-allowlisted API destination")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise ValueError("GH_TOKEN is not set")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ocfilo-f1-readonly-observer",
    })
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)
        link = response.headers.get("Link", "")
    if 'rel="next"' in link:
        # Never silently make READY_FOR_MERGE from an incomplete listing.
        raise ValueError("GitHub response paginated; observer fail closed")
    return result

def collect(pr_number):
    if type(pr_number) is not int or pr_number not in ALLOWED_PRS:
        raise ValueError("Only approved F1 test PRs 1 and 2")
    pr = get(f"/repos/{REPO}/pulls/{pr_number}")
    sha = pr["head"]["sha"]
    checks = get(f"/repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    statuses = get(f"/repos/{REPO}/commits/{sha}/status?per_page=100")
    reviews = get(f"/repos/{REPO}/pulls/{pr_number}/reviews?per_page=100")
    issue_comments = get(f"/repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    review_comments = get(f"/repos/{REPO}/pulls/{pr_number}/comments?per_page=100")
    head_again = get(f"/repos/{REPO}/pulls/{pr_number}")
    if head_again["head"]["sha"] != sha or head_again["state"] != pr["state"]:
        return {"state": "STALE_SNAPSHOT", "pr_number": pr_number,
                "dispatch_allowed": False, "head_sha": sha}
    def user(x):
        u = x.get("user") or {}
        return {"login": u.get("login"), "type": u.get("type")}
    snap = {
        "pr": {
            "number": pr["number"], "state": pr["state"], "merged": pr.get("merged"),
            "draft": pr["draft"], "mergeable": pr["mergeable"],
            "mergeable_state": pr["mergeable_state"],
            "requested_reviewers": pr.get("requested_reviewers", []),
            "requested_teams": pr.get("requested_teams", []),
            "head": {"sha": sha, "ref": pr["head"]["ref"],
                     "repo": {"full_name": pr["head"]["repo"]["full_name"]}},
            "base": {"ref": pr["base"]["ref"],
                     "repo": {"full_name": pr["base"]["repo"]["full_name"]}},
        },
        "checks": {"total_count": checks["total_count"], "check_runs": [
            {"id": z["id"], "name": z["name"], "head_sha": z["head_sha"],
             "status": z["status"], "conclusion": z["conclusion"],
             "app_slug": (z.get("app") or {}).get("slug")}
            for z in checks["check_runs"]]},
        "statuses": {"statuses": [
            {"context": z["context"], "state": z["state"],
             "sha": z.get("sha", sha), "created_at": z.get("created_at")}
            for z in statuses["statuses"]]},
        "reviews": [{"user": user(r), "state": r["state"]} for r in reviews],
        "issue_comments": [{"id": c["id"], "user": user(c)} for c in issue_comments],
        "review_comments": [{"id": c["id"], "user": user(c)} for c in review_comments],
    }
    return decide(snap)

def main():
    if len(sys.argv) != 3:
        raise SystemExit("Expected PR number and JSON output path")
    number = int(sys.argv[1])
    output = Path(sys.argv[2])
    try:
        result = collect(number)
    except (ValueError, KeyError, IndexError, urllib.error.URLError) as e:
        # Fail closed rather than accidentally marking a PR merge-ready.
        print("observer blocked: " + str(e), file=sys.stderr)
        raise SystemExit(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"PR #{number}: {result['state']} at {result['head_sha']};"
          f" dispatch_allowed={result['dispatch_allowed']}")
    # The observer NEVER launches a worker even if repair is recommended.
    if result["state"] in ("STALE_SNAPSHOT", "UNKNOWN"):
        raise SystemExit(2)

if __name__ == "__main__":
    main()
