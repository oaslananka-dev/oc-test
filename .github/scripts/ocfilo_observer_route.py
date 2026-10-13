#!/usr/bin/env python3
"""Read-only, fail-closed event scope gate for the F1 PR observer.

Only events referring to the CURRENT SHA of the targeted PR may cause its
expensive decision/policy jobs to run. No untrusted event field is a command.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
SHA = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_PRS = frozenset({1, 2})
QUALITY_WORKFLOW = "ocfilo F1 Test Quality Gate"
SEMGREP_APP = "semgrep-code-oaslananka"
SEMGREP_CHECK = "semgrep-cloud-platform/scan"


def route(event_name, event, pr_number, current):
    if type(pr_number) is not int or pr_number not in ALLOWED_PRS:
        raise ValueError("Invalid F1 PR target")
    if not isinstance(event, dict) or not isinstance(current, dict):
        raise ValueError("Invalid GitHub event or live PR")
    repo = (current.get("head") or {}).get("repo") or {}
    if (current.get("number") != pr_number
            or current.get("state") != "open"
            or repo.get("full_name") != REPO):
        return False, "not_open_f1_pr"
    live_sha = (current.get("head") or {}).get("sha")
    if not isinstance(live_sha, str) or not SHA.fullmatch(live_sha):
        raise ValueError("Invalid current PR SHA")
    if event_name == "workflow_dispatch":
        return True, "manual_current_pr"
    if event_name == "pull_request":
        pr = event.get("pull_request") or {}
        sha = (pr.get("head") or {}).get("sha")
        if pr.get("number") != pr_number:
            return False, "other_pr"
        if event.get("action") not in ("synchronize", "reopened", "ready_for_review"):
            return False, "not_supported_action"
        return (sha == live_sha), ("matched_current_sha" if sha == live_sha else "stale_pr_event")
    if event_name == "workflow_run":
        run = event.get("workflow_run") or {}
        head_repo = (run.get("head_repository") or {}).get("full_name")
        if (run.get("name") != QUALITY_WORKFLOW
                or run.get("status") != "completed"
                or head_repo != REPO):
            return False, "not_trusted_quality_run"
        sha = run.get("head_sha")
        if sha != live_sha:
            return False, "unrelated_or_stale_quality_run"
        return True, "current_quality_run"
    if event_name == "check_run":
        check = event.get("check_run") or {}
        app = (check.get("app") or {}).get("slug")
        if (app != SEMGREP_APP or check.get("name") != SEMGREP_CHECK
                or check.get("status") != "completed"):
            return False, "not_semgrep_completion"
        sha = check.get("head_sha")
        if sha != live_sha:
            return False, "unrelated_or_stale_check_run"
        linked = check.get("pull_requests")
        if isinstance(linked, list) and linked:
            linked_numbers = [r.get("number") for r in linked if isinstance(r, dict)]
            if pr_number not in linked_numbers:
                return False, "not_linked_to_target_pr"
        return True, "current_semgrep_check"
    return False, "unsupported_event"


def live_pr(pr_number):
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("Missing restricted GITHUB_TOKEN")
    url = f"https://api.github.com/repos/{REPO}/pulls/{pr_number}"
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ocfilo-f1-event-router"
    })
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Expected EVENT_NAME, EVENT_PATH, PR_NUMBER")
    event_name, event_path, number = sys.argv[1:]
    pr_number = int(number)
    if pr_number not in ALLOWED_PRS:
        raise SystemExit("Unexpected PR target")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    live = live_pr(pr_number)
    observe, reason = route(event_name, event, pr_number, live)
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        raise SystemExit("Missing GitHub Actions output path")
    sha = (live.get("head") or {}).get("sha") if observe else ""
    with open(output, "a", encoding="utf-8") as f:
        f.write(f"should_observe={'true' if observe else 'false'}\n")
        f.write(f"matched_sha={sha}\n")
    print(f"F1 event route PR #{pr_number}: {'OBSERVE' if observe else 'SKIP'} ({reason})")


if __name__ == "__main__":
    main()
