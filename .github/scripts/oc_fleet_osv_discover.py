#!/usr/bin/env python3
"""Read-only OSV candidate discovery, grounded in live GitHub CI evidence.

The existing SHA-bound OSV publisher remains opt-in. This selector can never
dispatch, publish, open or merge a PR. It produces a proposed exact-SHA policy
for an owner-authored existing PR, or an informational main-branch finding.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import oc_fleet_issue as github
import oc_fleet_osv_lockfile as osv
import oc_fleet_osv_publish as publisher

REPO = osv.REPO
MAX_OPEN = 20
JOB = re.compile(r"https://github\.com/oaslananka/adxl355/actions/runs/([1-9][0-9]*)/job/([1-9][0-9]*)\Z")
GOOD = frozenset({"success", "neutral", "skipped"})
EXPECTED = frozenset({publisher.CHECK_OSV, publisher.CHECK_AGGREGATE})


def require(ok, why):
    if not ok:
        raise ValueError(why)


def get_lock(target, sha):
    require(type(sha) is str and publisher.SHA.fullmatch(sha),
            "Invalid lockfile ref")
    data = github.gh(target, f"repos/{REPO}/contents/{osv.LOCK}?ref={sha}")
    require(type(data) is dict and data.get("type") == "file"
            and data.get("encoding") == "base64"
            and type(data.get("size")) is int and data["size"] <= 1_000_000
            and type(data.get("content")) is str, "Invalid lockfile evidence")
    raw=base64.b64decode(data["content"], validate=False)
    require(len(raw) == data["size"], "Lockfile content length differs")
    lock=json.loads(raw)
    require(type(lock) is dict, "Lockfile is not JSON object")
    return lock


def completed_checks(target, sha):
    result=github.gh(target,f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    require(type(result) is dict and type(result.get("total_count")) is int
            and type(result.get("check_runs")) is list
            and 2 <= result["total_count"] == len(result["check_runs"]) < 100,
            "Incomplete SHA-bound check evidence")
    checks=result["check_runs"]
    require(all(type(c) is dict and c.get("head_sha") == sha
                and c.get("status") == "completed" for c in checks),
            "Checks not all completed/current")
    failed=[c for c in checks if c.get("conclusion") not in GOOD]
    require(len(failed) == 2
            and {c.get("name") for c in failed} == EXPECTED
            and all(c.get("conclusion") == "failure"
                    and (c.get("app") or {}).get("slug") == "github-actions"
                    for c in failed), "Not exactly the two proven audit failures")
    ids={}
    run_ids=set()
    for c in failed:
        match=JOB.fullmatch(c.get("details_url") or "")
        require(match is not None and int(match.group(2)) == c.get("id"),
                "Audit checks not bound to real GitHub workflow jobs")
        run_ids.add(int(match.group(1)))
        ids[c["name"]]=c["id"]
    require(len(run_ids) == 1, "Audit failures originate from different runs")
    # All available check names are pinned, so a later required check cannot
    # silently disappear and be mistaken for success.
    names=[c["name"] for c in checks]
    require(len(names) == len(set(names)) and len(names) <= 45,
            "Duplicate/unsupported check names")
    required=sorted(c["name"] for c in checks
                    if c.get("conclusion") == "success"
                    or c.get("name") in EXPECTED)
    require(len(required) >= 3, "No other successful CI check")
    return run_ids.pop(), ids, required


def pr_shape(pr):
    if type(pr) is not dict or type(pr.get("number")) is not int:
        return False
    return (pr.get("state") == "open" and pr.get("draft") is False
            and (pr.get("user") or {}).get("login","").lower() == "oaslananka"
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == REPO
            and ((pr.get("base") or {}).get("repo") or {}).get("full_name") == REPO
            and (pr.get("base") or {}).get("ref") == "main"
            and type((pr.get("head") or {}).get("sha")) is str
            and publisher.SHA.fullmatch(pr["head"]["sha"]) is not None
            and type((pr.get("head") or {}).get("ref")) is str)


def proposed_pr_policy(target, pr):
    """Only produce a policy after the existing publisher agrees it is eligible."""
    if not pr_shape(pr):
        return None
    number=pr["number"]
    sha=pr["head"]["sha"]
    changes=github.gh(target,f"repos/{REPO}/pulls/{number}/files?per_page=100")
    if (type(changes) is not list or len(changes) != 1
            or changes[0].get("filename") != osv.LOCK
            or changes[0].get("status") != "modified"):
        return None
    run_id, ids, names=completed_checks(target,sha)
    run=github.gh(target,f"repos/{REPO}/actions/runs/{run_id}")
    require(type(run) is dict and run.get("head_sha") == sha
            and run.get("head_branch") == pr["head"]["ref"]
            and run.get("event") == "pull_request"
            and run.get("name") == "Dependency Audit"
            and run.get("conclusion") == "failure",
            "Audit run not bound to candidate PR")
    data={
        "repository":REPO, "pr":number, "head_sha":sha,
        "head_branch":pr["head"]["ref"],
        "workflow_run_id":run_id,
        "osv_check_run_id":ids[publisher.CHECK_OSV],
        "aggregate_check_run_id":ids[publisher.CHECK_AGGREGATE],
        "advisory_id":"GHSA-0000-0000-0000",
        "required_green_checks":names,
    }
    # Fetch official SARIF only through the same validated provenance path.
    # Advisory identity is filled from the SARIF, not PR title/comments.
    artifact_name=publisher.ARTIFACT
    with tempfile.TemporaryDirectory(prefix="oc-osv-discovery-") as folder:
        # Exact-run integrity and PR/review checks require a preliminary
        # advisory ID; the discovery run is bound by the checks/run above.
        artifacts=github.gh(target,f"repos/{REPO}/actions/runs/{run_id}/artifacts?per_page=100")
        require(type(artifacts) is dict and type(artifacts.get("total_count")) is int
                and type(artifacts.get("artifacts")) is list
                and artifacts["total_count"] == len(artifacts["artifacts"])
                and artifacts["total_count"] < 100,
                "Incomplete artifact listing")
        require(len([a for a in artifacts["artifacts"]
                     if a.get("name") == artifact_name
                     and a.get("expired") is False]) == 1,
                "Missing/ambiguous official OSV artifact")
        sarif=publisher.fetch_sarif(target,data,Path(folder))
    finding=osv.one_finding(sarif)
    data["advisory_id"]=finding["ghsa"]
    osv.validated_advisory(finding,osv.gh_advisory(finding["ghsa"]))
    osv.locked_package(get_lock(target,sha),finding)
    # Existing publisher is the final authority, including review state,
    # required checks, SHA race and real official artifact inventory.
    publisher.check_current(target,data)
    return data


def list_open(target):
    pulls=github.gh(target,f"repos/{REPO}/pulls?state=open&per_page={MAX_OPEN}")
    require(type(pulls) is list and len(pulls) < MAX_OPEN,
            "Open PR inventory paginated/invalid")
    return sorted(pulls,key=lambda p:p.get("number",0))


def discover_existing(target):
    result=[]
    for pr in list_open(target):
        if not pr_shape(pr):
            continue
        try:
            candidate=proposed_pr_policy(target,pr)
            if candidate is not None:
                result.append(candidate)
        except (ValueError,KeyError,TypeError):
            # One invalid/incomplete PR must not authorize any PR. Emit an
            # explicit blocked signal rather than silently declaring green.
            raise
    return result


def last_main_audit(target):
    """Inspect current main, failing closed on races and truncated history."""
    main=github.gh(target,f"repos/{REPO}/branches/main")
    sha=(main.get("commit") or {}).get("sha")
    require(type(sha) is str and publisher.SHA.fullmatch(sha),
            "Cannot verify current main SHA")
    runs=github.gh(target,
        f"repos/{REPO}/actions/workflows/dependency-audit.yml/runs?branch=main&per_page=10")
    require(type(runs) is dict and type(runs.get("total_count")) is int
            and type(runs.get("workflow_runs")) is list
            and len(runs["workflow_runs"]) <= 10
            and runs["total_count"] >= len(runs["workflow_runs"]),
            "Invalid workflow listing")
    latest=next((r for r in runs["workflow_runs"]
                 if r.get("head_sha") == sha and r.get("event") in ("push","schedule")),None)
    if latest is None:
        # A full page does not establish that no current-SHA run exists.
        # Never turn truncated history into an authoritative 'no audit' answer.
        state=("MAIN_AUDIT_HISTORY_INCOMPLETE"
               if len(runs["workflow_runs"]) == 10
               else "NO_CURRENT_MAIN_AUDIT")
        outcome={"state":state,"main_sha":sha}
    elif latest.get("status") != "completed":
        outcome={"state":"MAIN_AUDIT_PENDING","main_sha":sha,
                 "workflow_run_id":latest["id"]}
    elif latest.get("conclusion") == "success":
        outcome={"state":"MAIN_AUDIT_GREEN","main_sha":sha,
                 "workflow_run_id":latest["id"]}
    elif latest.get("conclusion") == "failure":
        outcome={"state":"MAIN_AUDIT_FAILED_REVIEW","main_sha":sha,
                 "workflow_run_id":latest["id"],
                 "note":"Inspect genuine OSV artifact; never open a PR from aggregate status alone"}
    else:
        outcome={"state":"MAIN_AUDIT_INCONCLUSIVE","main_sha":sha,
                 "workflow_run_id":latest["id"]}
    fresh=github.gh(target,f"repos/{REPO}/branches/main")
    fresh_sha=(fresh.get("commit") or {}).get("sha")
    require(type(fresh_sha) is str and publisher.SHA.fullmatch(fresh_sha),
            "Cannot revalidate current main SHA")
    if fresh_sha != sha:
        return {"state":"MAIN_HEAD_MOVED","old_sha":sha,"main_sha":fresh_sha}
    return outcome


def observe(target):
    policies=discover_existing(target)
    main=last_main_audit(target)
    return {"repository":REPO,"mode":"READ_ONLY",
            "publish_allowed":False,
            "existing_pr_candidates":policies,
            "main_audit":main}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--policy-dir",type=Path,default=None,
                   help="Optional private output for EXACT proposed PR policies")
    args=p.parse_args()
    target=github.identity("oaslananka")
    result=observe(target)
    if args.policy_dir is not None and result["existing_pr_candidates"]:
        d=args.policy_dir
        d.mkdir(parents=True,exist_ok=True,mode=0o700)
        require(not d.is_symlink() and d.stat().st_mode & 0o077 == 0,
                "Candidate policy directory must be private")
        for policy in result["existing_pr_candidates"]:
            filename=d/f"osv-pr-{policy['pr']}.json"
            # Never overwrite a historical decision for a different SHA.
            fd=os.open(filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                json.dump(policy,f,indent=2)
            publisher.policy_read(filename)
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,subprocess.SubprocessError,
            KeyError,TypeError,json.JSONDecodeError) as error:
        print("OSV_DISCOVERY_BLOCKED",type(error).__name__,
              str(error)[:200],file=sys.stderr)
        sys.exit(1)
