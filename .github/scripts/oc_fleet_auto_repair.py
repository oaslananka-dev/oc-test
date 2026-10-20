#!/usr/bin/env python3
"""Select only an evidence-matched Codacy bare-assert repair; never auto-merge.

Uses the existing scoped PR repair worker/publisher, rather than a second
dispatcher. All code execution decisions come from pinned check-run identities,
not arbitrary PR text, comments, or downloaded CI logs.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

import oc_fleet_scoped_repair as scoped

REPO = scoped.TARGET
FILE = "python/tests/test_spidev_adapter.py"
CHECK = "Codacy Static Code Analysis"
APP_ID = 56611
MESSAGE = ("Use of assert detected. The enclosed code will be removed "
           "when compiling to optimised byte code.")
REQUIRED = (
    CHECK, "Python (3.10)", "Python (3.11)", "Python (3.12)",
    "semgrep-cloud-platform/scan", "SonarQube Cloud",
    "SonarCloud Code Analysis", "Tests", "CodeQL (python)",
    "Cross-language Consistency",
)
MAX_OPEN = 30


def check_added_line(patch, line):
    """Map the new side of Git unified diff; only added lines qualify."""
    if not isinstance(patch, str) or type(line) is not int or line <= 0:
        return False
    current = None
    for text in patch.splitlines():
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", text)
        if match:
            current = int(match.group(1))
        elif current is None or text.startswith("\\ No newline"):
            continue
        elif text.startswith("+"):
            if current == line:
                # Ensure the finding matches a newly added bare assertion.
                return re.match(r"^\+\s*assert\s+\S", text) is not None
            current += 1
        elif text.startswith(" "):
            current += 1
        elif text.startswith("-"):
            continue
    return False


def list_open(target):
    items = scoped.worker.gh(target, f"repos/{REPO}/pulls?state=open&per_page={MAX_OPEN}")
    scoped.require(isinstance(items, list) and len(items) < MAX_OPEN,
                   "PR listing incomplete or paginated")
    return sorted(items, key=lambda p: p.get("number", 0))


def select(target, pr):
    """Return a fully pinned policy only for the one previously proven rule."""
    if not isinstance(pr, dict) or type(pr.get("number")) is not int:
        return None
    number = pr["number"]
    if (pr.get("state") != "open" or pr.get("draft") is not False
            or (pr.get("user") or {}).get("login", "").lower() != "oaslananka"
            or (pr.get("head") or {}).get("repo", {}).get("full_name") != REPO
            or (pr.get("base") or {}).get("repo", {}).get("full_name") != REPO
            or (pr.get("base") or {}).get("ref") != "main"):
        return None
    sha = (pr.get("head") or {}).get("sha")
    branch = (pr.get("head") or {}).get("ref")
    if not isinstance(sha, str) or not scoped.SHA.fullmatch(sha):
        return None
    if not isinstance(branch, str) or not branch:
        return None
    files = scoped.worker.gh(target,f"repos/{REPO}/pulls/{number}/files?per_page=100")
    if (not isinstance(files, list) or len(files) != 1
            or files[0].get("filename") != FILE
            or files[0].get("status") != "modified"
            or type(files[0].get("additions")) is not int
            or files[0]["additions"] > 40 or files[0]["additions"] < 1
            or type(files[0].get("deletions")) is not int
            or files[0]["deletions"] > 10):
        return None
    checks = scoped.worker.gh(target,
        f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    if (not isinstance(checks, dict)
            or type(checks.get("total_count")) is not int
            or not isinstance(checks.get("check_runs"), list)
            or checks["total_count"] != len(checks["check_runs"])
            or checks["total_count"] >= 100):
        return None
    runs=checks["check_runs"]
    if not runs or any(c.get("head_sha") != sha or
                       c.get("status") != "completed" for c in runs):
        return None
    bad=[c for c in runs if c.get("conclusion") not in
         ("success","skipped","neutral")]
    if len(bad) != 1:
        return None
    check=bad[0]
    if (check.get("name") != CHECK or check.get("conclusion") != "action_required"
            or (check.get("app") or {}).get("id") != APP_ID
            or type(check.get("id")) is not int or check["id"] < 1):
        return None
    passed={c.get("name") for c in runs if c.get("conclusion") == "success"}
    if not (set(REQUIRED) - {CHECK}).issubset(passed):
        return None
    notes=scoped.worker.gh(target,
        f"repos/{REPO}/check-runs/{check['id']}/annotations?per_page=100")
    if (not isinstance(notes, list) or len(notes) != 1
            or notes[0].get("path") != FILE
            or notes[0].get("message") != MESSAGE
            or notes[0].get("start_line") != notes[0].get("end_line")
            or not check_added_line(files[0].get("patch"),notes[0].get("start_line"))):
        return None
    # The owner-approved goal is constant; no untrusted GitHub text is used.
    goal=("In the newly added SPI test, replace the bare assert flagged by "
          "Codacy with the equivalent unittest.TestCase assertion, preserving "
          "the exact test behavior. Only edit python/tests/test_spidev_adapter.py.")
    return dict(repository=REPO, pr=number, head_sha=sha,
                head_branch=branch,check_name=CHECK,
                check_run_id=check["id"],check_app_id=APP_ID,
                check_conclusion="action_required",allowed_paths=[FILE],
                required_green_checks=list(REQUIRED),repair_goal=goal)


def worker_capacity():
    """Bound the worker workflow to two active runs without scanning history."""
    account=scoped.worker.identity("MakerParsDev")
    busy=0
    for status in ("queued","in_progress","waiting","requested"):
        data=scoped.worker.gh(
            account,f"repos/{scoped.RUNNER}/actions/workflows/oc-fleet-worker.yml/"
                    f"runs?status={status}&per_page=100")
        scoped.require(isinstance(data,dict) and
                       type(data.get("total_count")) is int and
                       isinstance(data.get("workflow_runs"),list) and
                       data["total_count"] == len(data["workflow_runs"])
                       and data["total_count"] < 100,
                       "Incomplete active worker inventory")
        busy += data["total_count"]
    scoped.require(busy < 2, "Worker pool at concurrency capacity")
    return True


def policy_path(folder, number):
    return Path(folder)/f"pr-{number}.json"


def store_policy(folder, policy):
    folder=Path(folder)
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    scoped.require(not folder.is_symlink()
                   and folder.stat().st_mode & 0o077 == 0,"Policy folder not private")
    path=policy_path(folder,policy["pr"])
    # Do not replace an existing decision for a different SHA or changed rule.
    if path.exists():
        scoped.require(scoped.load_policy(path) == policy,
                       "Existing policy does not match live PR; owner reconciliation required")
        return path
    with path.open("x", encoding="utf-8") as f:
        os.fchmod(f.fileno(),0o600)
        json.dump(policy,f,indent=2)
    scoped.load_policy(path)
    return path


def poll(folder,db,live=False):
    """At most one repair per cycle; reconcile prior published receipts first."""
    target=scoped.worker.identity("oaslananka")
    c=scoped.database(db)
    try:
        records=c.execute(
            "SELECT pr,state FROM scoped_repairs WHERE repo=? ORDER BY id DESC",
            (REPO,)).fetchall()
    finally:
        c.close()
    seen=set()
    for number,state in records:
        if number in seen:
            continue
        seen.add(number)
        if state == "PUBLISHED":
            path=policy_path(folder,number)
            scoped.require(path.exists(),"Published receipt missing owner policy")
            if live:
                try:
                    return scoped.reconcile(path,db)
                except ValueError:
                    print("AUTO_REPAIR_WAIT_OR_BLOCKED",number,flush=True)
                    return "not_ready"
            print("AUTO_REPAIR_RECONCILE_PLAN",number,flush=True)
            return "reconcile_plan"
        if state in ("RESERVED","DISPATCHED"):
            print("AUTO_REPAIR_PRIOR_RUN_BLOCKED",number,state,flush=True)
            return "blocked"
    for pr in list_open(target):
        number=pr["number"]
        if number in seen:
            continue  # No blind retry even after a proven failure.
        policy=select(target,pr)
        if policy is None:
            continue
        # Reuse the full trusted snapshot/review/status checks from the proven publisher.
        scoped.fetch_verified(policy,target)
        print("AUTO_REPAIR_CANDIDATE",number,policy["head_sha"][:12],flush=True)
        if not live:
            return "eligible"
        worker_capacity()
        file=store_policy(folder,policy)
        return scoped.execute(file,db,True)
    print("AUTO_REPAIR_NO_ELIGIBLE_PR",flush=True)
    return "none"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--run",action="store_true",help="Enable only the pinned Codacy rule")
    p.add_argument("--policy-dir",type=Path,
                   default=Path.home()/".local/state/oc-fleet/auto-policies")
    p.add_argument("--db",type=Path,
                   default=Path.home()/".local/state/oc-fleet/scoped-repairs.sqlite3")
    a=p.parse_args()
    poll(a.policy_dir,a.db,a.run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError,OSError,KeyError,TypeError) as error:
        print("AUTO_REPAIR_BLOCKED",type(error).__name__,
              str(error)[:180],file=sys.stderr)
        sys.exit(1)
