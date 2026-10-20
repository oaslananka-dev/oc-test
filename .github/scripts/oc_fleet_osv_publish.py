#!/usr/bin/env python3
"""Opt-in, two-check OSV repair publisher on the SAME existing ADXL355 PR.

Model-free. Never creates a PR, force-pushes, merges, or enrolls the timer.
Unknown publish outcomes remain reserved for manual reconciliation.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

import oc_fleet_issue as github
import oc_fleet_osv_lockfile as osv
from oc_fleet_identity import github_user_identity, verify_commit
from oc_fleet_pr_repair import confirm_new_head
import oc_fleet_scoped_repair as scoped

REPO = osv.REPO
CHECK_OSV = "OSV repository audit / osv-scan"
CHECK_AGGREGATE = "Dependency Audit"
ARTIFACT = "OSV Scanner SARIF file"
SHA = re.compile(r"[a-f0-9]{40}\Z")
FIELDS = {"repository","pr","head_sha","head_branch","workflow_run_id",
          "osv_check_run_id","aggregate_check_run_id","advisory_id",
          "required_green_checks"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def policy_read(path):
    p=Path(path)
    require(p.is_file() and not p.is_symlink() and p.stat().st_size < 2500
            and (p.stat().st_mode & 0o077) == 0,"Owner-private policy required")
    d=json.loads(p.read_text())
    require(type(d) is dict and set(d) == FIELDS,"Policy schema mismatch")
    require(d["repository"] == REPO and type(d["pr"]) is int
            and 1 <= d["pr"] < 1000000, "Repository or PR not enabled")
    require(type(d["head_sha"]) is str and SHA.fullmatch(d["head_sha"]),
            "Expected immutable HEAD SHA")
    branch=d["head_branch"]
    require(type(branch) is str and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,85}",branch)
            and ".." not in branch and "//" not in branch
            and not branch.endswith(("/", ".")), "Invalid branch")
    for name in ("workflow_run_id","osv_check_run_id","aggregate_check_run_id"):
        require(type(d[name]) is int and d[name] > 0,"Missing pinned run/check IDs")
    require(type(d["advisory_id"]) is str and osv.GHSA.fullmatch(d["advisory_id"]),
            "Invalid GHSA")
    required=d["required_green_checks"]
    require(type(required) is list and 2 <= len(required) <= 45
            and len(set(required)) == len(required)
            and CHECK_OSV in required and CHECK_AGGREGATE in required
            and all(type(n) is str and 2 <= len(n) < 180 for n in required),
            "Explicit post-repair checks required")
    return d


def get_pr(target,d):
    pr=github.gh(target,f"repos/{REPO}/pulls/{d['pr']}")
    require(pr.get("number") == d["pr"]
            and pr.get("state") == "open" and pr.get("merged") is False
            and pr.get("draft") is False
            and (pr.get("user") or {}).get("login","").lower() == "oaslananka"
            and (pr.get("head") or {}).get("sha") == d["head_sha"]
            and (pr.get("head") or {}).get("ref") == d["head_branch"]
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == REPO
            and ((pr.get("base") or {}).get("repo") or {}).get("full_name") == REPO
            and (pr.get("base") or {}).get("ref") == "main"
            and pr.get("mergeable") is not False
            and pr.get("mergeable_state") != "dirty",
            "PR identity, HEAD, or merge state not authorized")
    return pr


def published_head(target,d):
    pr=github.gh(target,f"repos/{REPO}/pulls/{d['pr']}")
    require(pr.get("state") == "open"
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == REPO
            and (pr.get("head") or {}).get("ref") == d["head_branch"]
            and ((pr.get("base") or {}).get("repo") or {}).get("full_name") == REPO,
            "PR branch changed")
    return (pr.get("head") or {}).get("sha")


def check_current(target,d):
    pr=get_pr(target,d)
    sha=d["head_sha"]
    runs=github.gh(target,f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    require(type(runs) is dict and type(runs.get("total_count")) is int
            and type(runs.get("check_runs")) is list
            and 2 <= runs["total_count"] == len(runs["check_runs"]) < 100,
            "Incomplete current check-run evidence")
    checks=runs["check_runs"]
    require(all(c.get("head_sha") == sha and c.get("status") == "completed"
                for c in checks),"Other CI still running or stale")
    bad=[c for c in checks if c.get("conclusion") not in ("success","skipped","neutral")]
    require(len(bad) == 2, "Expected exactly two failed OSV and aggregate checks")
    expected={CHECK_OSV:d["osv_check_run_id"],
              CHECK_AGGREGATE:d["aggregate_check_run_id"]}
    require({c.get("name"):c.get("id") for c in bad} == expected
            and all((c.get("app") or {}).get("slug") == "github-actions"
                    and c.get("conclusion") == "failure" for c in bad),
            "Failure ID/name/publisher mismatch")
    names={c.get("name") for c in checks}
    require(set(d["required_green_checks"]).issubset(names),
            "Required checks missing on failed SHA")
    statuses=github.gh(target,f"repos/{REPO}/commits/{sha}/status")
    require(type(statuses) is dict and type(statuses.get("statuses")) is list
            and all(s.get("state") == "success" for s in statuses["statuses"]),
            "Commit status not green")
    reviews=github.gh(target,f"repos/{REPO}/pulls/{d['pr']}/reviews?per_page=100")
    inline=github.gh(target,f"repos/{REPO}/pulls/{d['pr']}/comments?per_page=100")
    require(type(reviews) is list and len(reviews) < 100 and
            type(inline) is list and not inline,
            "Review evidence incomplete or inline feedback exists")
    require(not any(r.get("state") in ("CHANGES_REQUESTED","PENDING")
                    for r in reviews), "Review change request blocks publication")
    run=github.gh(target,f"repos/{REPO}/actions/runs/{d['workflow_run_id']}")
    require(run.get("id") == d["workflow_run_id"]
            and run.get("head_sha") == sha and run.get("name") == "Dependency Audit"
            and run.get("event") == "pull_request"
            and run.get("status") == "completed"
            and run.get("conclusion") == "failure"
            and (run.get("repository") or {}).get("full_name") == REPO,
            "Wrong or stale official Dependency Audit workflow")
    # Tie the OSV check run to the exact workflow run.
    require(all(f"/actions/runs/{d['workflow_run_id']}/job/{c['id']}"
                in (c.get("details_url") or "") for c in bad),
            "OSV/aggregate check does not belong to the pinned workflow run")
    artifacts=github.gh(target,
        f"repos/{REPO}/actions/runs/{d['workflow_run_id']}/artifacts?per_page=100")
    require(type(artifacts) is dict and type(artifacts.get("total_count")) is int
            and type(artifacts.get("artifacts")) is list
            and artifacts["total_count"] == len(artifacts["artifacts"])
            and artifacts["total_count"] < 100,
            "Incomplete official SARIF artifacts")
    matches=[a for a in artifacts["artifacts"]
             if a.get("name") == ARTIFACT and a.get("expired") is False]
    require(len(matches) == 1,"Require one live official SARIF artifact")
    require(published_head(target,d) == sha,"PR changed during checks")
    return matches[0]


def fetch_sarif(target,d,destination):
    # Artifact is downloaded only after its GitHub-run provenance is checked.
    github.cmd(["gh","run","download",str(d["workflow_run_id"]),"-R",REPO,
                "--name",ARTIFACT,"--dir",str(destination)],target,deadline=45)
    items=list(destination.iterdir())
    require(len(items) == 1 and items[0].name == "dependency-audit-osv.sarif"
            and items[0].is_file() and not items[0].is_symlink()
            and items[0].stat().st_size < 500_000,
            "Unexpected SARIF artifact contents")
    return json.loads(items[0].read_text())


def local_patch(target,d,sarif,checkout):
    finding=osv.one_finding(sarif)
    require(finding["ghsa"] == d["advisory_id"],
            "SARIF GHSA differs from approved incident")
    advisory=osv.gh_advisory(finding["ghsa"])
    # Use only one selected ref; replay itself requires a clean worktree.
    result=osv.replay(checkout,sarif,advisory)
    require(result["changed_file"] == osv.LOCK and result["publish"] is False,
            "Unexpected model-free replay")
    change=github.cmd(["git","-C",str(checkout),"diff","--name-only"],target)
    require(change.stdout.strip() == osv.LOCK,"Scope changed unexpectedly")
    return result


def publish(target,d,sarif):
    require(published_head(target,d) == d["head_sha"],"PR changed before patch")
    with tempfile.TemporaryDirectory(prefix="oc-osv-publish-") as temp:
        checkout=Path(temp)/"repo"
        github.cmd(["gh","repo","clone",REPO,str(checkout),"--","--quiet"],target)
        github.cmd(["git","-C",str(checkout),"checkout","--detach",d["head_sha"]],target)
        github.cmd(["git","-C",str(checkout),"config","core.autocrlf","false"],target)
        github.cmd(["git","-C",str(checkout),"reset","--hard",d["head_sha"]],target)
        result=local_patch(target,d,sarif,checkout)
        require(published_head(target,d) == d["head_sha"],
                "PR changed during deterministic repair")
        actor=github.gh(target,"user")
        require(actor.get("login","").lower() == "oaslananka",
                "Incorrect trusted publisher")
        name,email=github_user_identity(actor)
        for key,val in (("user.name",name),("user.email",email)):
            github.cmd(["git","-C",str(checkout),"config",key,val],target)
        github.cmd(["git","-C",str(checkout),"add","--",osv.LOCK],target)
        github.cmd(["git","-C",str(checkout),"commit","-m",
                    "fix(node): patch reviewed OSV vulnerable dev dependency"],target)
        verify_commit(checkout,name,email)
        commit=github.cmd(["git","-C",str(checkout),"rev-parse","HEAD"],target).stdout.strip()
        require(SHA.fullmatch(commit),"Bad repaired commit")
        github.cmd(["git","-C",str(checkout),"-c","credential.helper=",
                    "-c","credential.helper=!gh auth git-credential",
                    "push","origin",f"HEAD:refs/heads/{d['head_branch']}"],target)
        confirm_new_head(lambda:published_head(target,d),d["head_sha"],commit)
        return commit,result


def connect(db):
    p=Path(db)
    p.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(p,isolation_level=None,timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    c.execute("""CREATE TABLE IF NOT EXISTS osv_pr_repairs (
      id INTEGER PRIMARY KEY, repository TEXT NOT NULL, pr INTEGER NOT NULL,
      original_sha TEXT NOT NULL, workflow_run_id INTEGER NOT NULL,
      state TEXT NOT NULL, published_sha TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return c


def reserve(c,d):
    c.execute("BEGIN IMMEDIATE")
    try:
        found=c.execute("SELECT id,state FROM osv_pr_repairs WHERE repository=? AND pr=?",
                        (REPO,d["pr"])).fetchall()
        require(not found, "An OSV repair receipt already exists for this PR")
        job=c.execute("INSERT INTO osv_pr_repairs "
           "(repository,pr,original_sha,workflow_run_id,state) VALUES (?,?,?,?,?)",
           (REPO,d["pr"],d["head_sha"],d["workflow_run_id"],"RESERVED")).lastrowid
        c.commit()
        return job
    except Exception:
        c.rollback()
        raise


def transition(c,job,old,new,sha=None):
    require((old,new) in (("RESERVED","PUBLISHED"),("PUBLISHED","CI_SUCCEEDED")),
            "Invalid OSV receipt transition")
    if new == "PUBLISHED":
        require(type(sha) is str and SHA.fullmatch(sha),"Commit receipt required")
    c.execute("BEGIN IMMEDIATE")
    try:
        row=c.execute("SELECT state FROM osv_pr_repairs WHERE id=?",(job,)).fetchone()
        require(row and row[0] == old,"Receipt state changed")
        c.execute("UPDATE osv_pr_repairs SET state=?,published_sha=COALESCE(?,published_sha)"
                  " WHERE id=?",(new,sha,job))
        c.commit()
    except Exception:
        c.rollback()
        raise


def verify_ci(target,d,published):
    require(published_head(target,d) == published and published != d["head_sha"],
            "Repaired PR moved before CI verification")
    checks=github.gh(target,f"repos/{REPO}/commits/{published}/check-runs?per_page=100")
    require(type(checks) is dict and type(checks.get("total_count")) is int
            and type(checks.get("check_runs")) is list
            and 0 < checks["total_count"] == len(checks["check_runs"]) < 100,
            "Incomplete post-publish CI")
    runs=checks["check_runs"]
    require(all(r.get("head_sha") == published and r.get("status") == "completed"
                and r.get("conclusion") in ("success","neutral","skipped") for r in runs),
            "CI pending or failing on new SHA")
    green={r.get("name") for r in runs if r.get("conclusion") == "success"}
    require(set(d["required_green_checks"]).issubset(green),
            "Required OSV and aggregate jobs must be green")
    statuses=github.gh(target,f"repos/{REPO}/commits/{published}/status")
    require(type(statuses) is dict and type(statuses.get("statuses")) is list
            and all(s.get("state") == "success" for s in statuses["statuses"]),
            "Post-repair commit status not green")
    require(published_head(target,d) == published,"PR moved during CI verification")


def execute(path,db,mode):
    d=policy_read(path)
    target=github.identity("oaslananka")
    if mode=="verify-ci":
        c=connect(db)
        try:
            row=c.execute("SELECT id,state,original_sha,published_sha "
                          "FROM osv_pr_repairs WHERE repository=? AND pr=?",
                          (REPO,d["pr"])).fetchone()
            require(row and row[1] == "PUBLISHED" and row[2] == d["head_sha"]
                    and type(row[3]) is str, "No matching published receipt")
            verify_ci(target,d,row[3])
            transition(c,row[0],"PUBLISHED","CI_SUCCEEDED")
            print("OSV_PR_CI_SUCCEEDED",d["pr"],row[3],flush=True)
            return row[3]
        finally:
            c.close()
    check_current(target,d)
    with tempfile.TemporaryDirectory(prefix="osv-provenance-") as t:
        sarif=fetch_sarif(target,d,Path(t))
    finding=osv.one_finding(sarif)
    require(finding["ghsa"] == d["advisory_id"],"Unexpected OSV finding")
    fix=osv.validated_advisory(finding,osv.gh_advisory(finding["ghsa"]))
    print("OSV_PR_ELIGIBLE",d["pr"],d["head_sha"][:12],
          finding["package"],finding["old"],fix,flush=True)
    if mode=="plan":
        return "read_only"
    c=connect(db)
    try:
        job=reserve(c,d)
        # Crash or ambiguous push deliberately leaves RESERVED for reconciliation.
        commit,_=publish(target,d,sarif)
        transition(c,job,"RESERVED","PUBLISHED",commit)
        print("OSV_PR_PUBLISHED",d["pr"],commit,flush=True)
        return commit
    finally:
        c.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--policy",type=Path,required=True)
    p.add_argument("--db",type=Path,
                   default=Path.home()/".local/state/oc-fleet/osv-pr-repairs.sqlite3")
    m=p.add_mutually_exclusive_group()
    m.add_argument("--run",action="store_true",help="Opt-in same-PR publication only")
    m.add_argument("--verify-ci",action="store_true",help="Verify fresh required checks")
    args=p.parse_args()
    mode="verify-ci" if args.verify_ci else "run" if args.run else "plan"
    execute(args.policy,args.db,mode)


if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,subprocess.SubprocessError,KeyError,
            json.JSONDecodeError,TypeError) as e:
        print("OSV_PR_BLOCKED",type(e).__name__,str(e)[:180],file=sys.stderr)
        sys.exit(1)
