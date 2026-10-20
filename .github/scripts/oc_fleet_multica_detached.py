#!/usr/bin/env python3
"""Restartable Multica dispatch/reconcile without blocking on active workers.

Only the trusted operator publishes after independently checking a completed,
successful GitHub Actions worker and the original durable run receipt.
"""
import json
import os
from pathlib import Path
import re
import subprocess

import oc_fleet_multica as handoff
import oc_fleet_multica_poll as poller

STATE="worker_dispatched"
PILOT_REPO="oaslananka-dev/oc-test"
PR_URL=re.compile(r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)\Z")


def require(ok,message):
    if not ok: raise ValueError(message)


def execute(args, timeout=30):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout


def listing(project, metadata=None):
    argv=["multica","issue","list","--project",project]
    if metadata:
        argv.extend(["--metadata",metadata])
    argv.extend(["--limit","100","--fields",
                 "identifier,status,project_id,assignee_type,metadata","--output","json"])
    found=json.loads(execute(argv,60))
    require(type(found) is dict and found.get("has_more") is False
            and type(found.get("issues")) is list
            and len(found["issues"]) <= 100,
            "Incomplete Multica detached-state listing")
    return found["issues"]


def update(key,field,value,kind="string"):
    require(handoff.ISSUE_RE.fullmatch(key) is not None,"Invalid Multica issue ID")
    require(field in ("fleet_ready","fleet_run_id","fleet_result","fleet_pull_request"),
            "Unapproved metadata field")
    execute(["multica","issue","metadata","set",key,"--key",field,
             "--value",str(value).lower() if kind=="bool" else str(value),
             "--type",kind],30)


def status(key,state):
    require(state in ("in_progress","in_review","blocked"),
            "Unsupported issue state")
    execute(["multica","issue","status",key,state,"--no-start"],30)


def receipt_for(meta):
    path=poller.controller_receipt(meta)
    require(path.is_file() and not path.is_symlink()
            and 0 < path.stat().st_size <= 24000,
            "Missing authentic local worker dispatch receipt")
    doc=json.loads(path.read_text())
    manifest=doc.get("manifest")
    require(type(manifest) is dict
            and manifest.get("repository","").lower() == meta["fleet_target_repo"].lower()
            and manifest.get("issue_key")=="GH-"+meta["fleet_target_issue"]
            and manifest.get("allowed_paths")==[
                meta["fleet_allowed_path"],*handoff.extra_allowed_paths(meta)]
            and manifest.get("multica_issue") is not None
            and doc.get("worker_repo","").lower()==meta["fleet_worker_repo"].lower()
            and type(doc.get("run_id")) is int and doc["run_id"] > 0,
            "Worker receipt does not match selected Multica task")
    return doc


def mark_dispatched(key,run_id):
    # Ordered updates allow a later cycle to recover any interrupted stage.
    update(key,"fleet_run_id",str(run_id))
    update(key,"fleet_ready",False,"bool")
    update(key,"fleet_result",STATE)
    status(key,"in_progress")


def parse_output(stdout,expected,mode):
    lines=[]
    for raw in stdout.splitlines():
        if not raw.startswith("{"):continue
        try:doc=json.loads(raw)
        except json.JSONDecodeError:continue
        if type(doc) is dict and doc.get("status")==mode:lines.append(doc)
    require(len(lines)==1,"Expected exactly one trusted controller output")
    return lines[0]


def dispatch_one(item, project, live, claim_dir):
    key=item.get("identifier")
    require(type(key) is str and handoff.ISSUE_RE.fullmatch(key),"Bad Multica task identity")
    fresh=handoff.read_issue(key)
    meta=handoff.validate_issue(fresh,key,project,live)
    if poller.controller_receipt(meta).exists() or (claim_dir/(key+".claim")).exists():
        return "already_claimed"
    if not live:
        return "planned"
    claim_dir.mkdir(parents=True,mode=0o700,exist_ok=True)
    claim=claim_dir/(key+".claim")
    try:
        descriptor=os.open(claim,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        return "already_claimed"
    with os.fdopen(descriptor,"w") as f:f.write(key+"\n")
    command=handoff.controller_command(key,meta,True)+["--dispatch-only"]
    # Only starts a trusted worker, does not wait for it or publish a PR.
    out=execute(command,180)
    result=parse_output(out,meta,"WORKER_DISPATCHED")
    doc=receipt_for(meta)
    rid=str(doc["run_id"])
    require(result.get("run_id")==rid
            and result.get("worker_repo","").lower()==meta["fleet_worker_repo"].lower()
            and result.get("target_repo","").lower()==meta["fleet_target_repo"].lower()
            and result.get("target_issue")==meta["fleet_target_issue"]
            and doc["manifest"].get("multica_issue")==key,
            "Unverified dispatch-to-Multica binding")
    mark_dispatched(key,rid)
    print("DETACHED_WORKER",key,rid,flush=True)
    return "dispatched"


def recover_claims(project,claim_dir,live):
    """Recover partial Multica metadata updates after a confirmed dispatch.

    A claim alone never authorizes a retry or a new worker. The complete
    local controller receipt must match an existing Multica job.
    """
    repaired=0
    for item in listing(project):
        key=item.get("identifier")
        if type(key) is not str or not handoff.ISSUE_RE.fullmatch(key):
            raise ValueError("Invalid claimed issue")
        if item.get("status") not in ("backlog","todo","in_progress"):
            continue
        meta=item.get("metadata") or {}
        if meta.get("fleet_result") in ("completed","pr_created","worker_failed","worker_blocked"):
            continue
        if not (claim_dir/(key+".claim")).is_file():
            continue
        receipt=poller.controller_receipt(meta)
        if not receipt.is_file():
            # Unknown dispatch status: keep the claim and require reconciliation.
            print("DETACHED_UNKNOWN_CLAIM",key,flush=True)
            continue
        doc=receipt_for(meta)
        require(doc["manifest"]["multica_issue"]==key,"Claim/receipt owner mismatch")
        if (meta.get("fleet_result")==STATE
                and meta.get("fleet_run_id")==str(doc["run_id"])
                and meta.get("fleet_ready") is False
                and item["status"]=="in_progress"):
            continue
        if live:
            mark_dispatched(key,doc["run_id"])
        repaired+=1
        print("DETACHED_RECOVERED" if live else "DETACHED_RECOVERY_PLAN",
              key,doc["run_id"],flush=True)
    return repaired


def resume_one(item,project,live):
    key=item.get("identifier")
    require(type(key) is str and handoff.ISSUE_RE.fullmatch(key),"Bad pending issue")
    fresh=handoff.read_issue(key)
    meta=fresh.get("metadata") or {}
    require(fresh.get("project_id")==project
            and fresh.get("status")=="in_progress"
            and meta.get("fleet_ready") is False
            and meta.get("fleet_result")==STATE,
            "Issue is no longer pending a worker")
    doc=receipt_for(meta)
    require(doc["manifest"]["multica_issue"]==key
            and str(doc["run_id"])==meta.get("fleet_run_id"),
            "Worker run not bound to Multica")
    repo=meta["fleet_worker_repo"]
    run=json.loads(execute(["gh","api",f"repos/{repo}/actions/runs/{doc['run_id']}"],35))
    require(run.get("id")==doc["run_id"]
            and (run.get("repository") or {}).get("full_name","").lower()==repo.lower()
            and run.get("event")=="workflow_dispatch"
            and run.get("head_branch")=="main"
            and run.get("run_attempt")==1,
            "Untrusted worker run details")
    if run.get("status")!="completed":
        return "pending"
    if run.get("conclusion")!="success":
        if live:
            update(key,"fleet_result","worker_failed")
            status(key,"blocked")
        return "failed"
    if not live:
        return "ready_to_publish"
    command=handoff.controller_command(key,meta,True)
    command[2]="resume"
    out=execute(command,540)
    result=parse_output(out,meta,"PR_CREATED")
    match=PR_URL.fullmatch(result.get("url") or "")
    require(match is not None and match.group(1).lower()==meta["fleet_target_repo"].lower()
            and result.get("run_id")==str(doc["run_id"]),
            "Unverified resume PR or worker identity")
    update(key,"fleet_pull_request",match.group(2))
    update(key,"fleet_result","pr_created")
    status(key,"in_review")
    print("DETACHED_PR_CREATED",key,match.group(2),flush=True)
    return "published"


def cycle(project,live,claim_dir,budget):
    recover_claims(project,claim_dir,live)
    pending=listing(project,"fleet_result=worker_dispatched")
    require(len(pending) <= 100,"Pending inventory incomplete")
    # Finalization of pilot PRs waits for all still-running workers to
    # publish. Otherwise a main merge could stale a slow worker's base SHA.
    still_running=False
    for issue in sorted(pending,key=lambda i:i.get("identifier","")):
        try:
            state=resume_one(issue,project,live)
        except (ValueError,OSError,subprocess.SubprocessError,KeyError,
                TypeError,json.JSONDecodeError) as error:
            # Preserve the existing receipt/claim on ambiguous publication.
            # Other tasks may still be admitted, but no pilot PR merges while
            # an earlier worker result remains unreconciled.
            still_running=True
            print("DETACHED_RECONCILE_BLOCKED",issue.get("identifier"),
                  type(error).__name__,str(error)[:150],flush=True)
            continue
        still_running|=state in ("pending","ready_to_publish")
        print("DETACHED_RECONCILE",issue["identifier"],state,flush=True)
    if not still_running:
        poller.finalizer.poll(project,live,poller.controller_receipt)
    return poller.poll(project,live,claim_dir,budget,dispatch_handler=dispatch_one)
