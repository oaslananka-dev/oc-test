#!/usr/bin/env python3
"""Read-only, identity-pinned GitHub run reconciliation for OC Fleet.

This deliberately cannot cancel, pause, redirect, dispatch or merge a run.
The proposal output is a non-executable review envelope, not authorization.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from oc_fleet_observe import observation, read_only, recent, RUN_ID, ATTEMPT

REPO = "oaslananka-dev/oc-test"
WORKFLOW = ".github/workflows/oc-fleet-worker.yml"
SHA = re.compile(r"[0-9a-f]{40}\Z")
ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}
FINAL = {"success", "failure", "cancelled", "timed_out", "action_required",
         "skipped", "neutral", "stale", "startup_failure"}


def get_github_run(run):
    """Use gh's existing operator read permission, never bearer tokens/output."""
    if not isinstance(run, str) or not RUN_ID.fullmatch(run):
        raise ValueError("invalid run identity")
    output = subprocess.run(
        ["gh", "api", f"repos/{REPO}/actions/runs/{run}"],
        text=True, capture_output=True, check=True, timeout=15)
    result = json.loads(output.stdout)
    if not isinstance(result, dict):
        raise ValueError("invalid GitHub response")
    return result


def verified_state(run, attempt, result):
    """Reject a run from another repo/workflow/attempt, regardless of status."""
    if (not isinstance(run, str) or not RUN_ID.fullmatch(run)
            or not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt)
            or not isinstance(result, dict)):
        raise ValueError("invalid GitHub comparison")
    identity = result.get("repository")
    if (type(result.get("id")) is not int
            or result["id"] != int(run)
            or type(result.get("run_attempt")) is not int
            or result["run_attempt"] != int(attempt)
            or not isinstance(identity, dict)
            or identity.get("full_name") != REPO
            or result.get("head_branch") != "main"
            or result.get("event") != "workflow_dispatch"):
        raise ValueError("GitHub source/attempt mismatch")
    path = result.get("path")
    if path not in (WORKFLOW, WORKFLOW + "@main", WORKFLOW + "@refs/heads/main"):
        raise ValueError("unapproved workflow")
    sha = result.get("head_sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("invalid GitHub head SHA")
    status, conclusion = result.get("status"), result.get("conclusion")
    if status == "completed":
        if conclusion not in FINAL:
            raise ValueError("invalid completed conclusion")
        state = "completed"
    elif status in ACTIVE:
        if conclusion is not None:
            raise ValueError("nonfinal run with a conclusion")
        # queued/waiting != executing. Preserve GitHub's exact status.
        state = "nonterminal_at_github"
    else:
        raise ValueError("unknown GitHub status")
    return {
        "verified": True,
        "source": "github_actions_rest_get_workflow_run",
        "repository": REPO,
        "run_id": run,
        "attempt": attempt,
        "head_sha": sha,
        "workflow": WORKFLOW,
        "state": state,
        "github_status": status,
        "github_conclusion": conclusion,
        "run_url": f"https://github.com/{REPO}/actions/runs/{run}",
        "telemetry_proves_liveness": False,
    }


def snapshot(db, run, attempt, limit=20, fetcher=None):
    """Fuse bounded local telemetry with the authoritative run read."""
    obs = observation(db, run, attempt, limit)
    fetcher = fetcher or get_github_run
    github = verified_state(run, attempt, fetcher(run))
    # Do not infer signed telemetry identity from a caller's run URL alone.
    return {"observation": obs, "github": github}


def cancel_proposal(item, now=None):
    """Review only. No write operation, cancellation credential, or approval."""
    github = item["github"]
    obs = item["observation"]
    if not github["verified"] or github["state"] != "nonterminal_at_github":
        raise ValueError("no cancellable active workflow")
    if obs["run_id"] != github["run_id"] or obs["attempt"] != github["attempt"]:
        raise ValueError("source identity mismatch")
    if not isinstance(obs["multica_issue"], str):
        raise ValueError("missing registered issue")
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    now = now.astimezone(timezone.utc)
    fields = {
        "action": "propose_cancel", "repository": REPO,
        "run_id": github["run_id"], "run_attempt": github["attempt"],
        "head_sha": github["head_sha"], "workflow": WORKFLOW,
        "registered_multica_issue": obs["multica_issue"],
    }
    request_id = hashlib.sha256(json.dumps(
        fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "proposal_id": request_id,
        **fields,
        "requested_at_utc": now.isoformat().replace("+00:00", "Z"),
        "expires_at_utc": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "approval": "required_not_granted",
        "manifest_task_identity": "not_verified",
        "executable": False,
        "required_before_execution": [
            "explicit owner approval for this exact run and attempt",
            "independently verified worker manifest and task identity",
            "fresh exact-head GitHub recheck and least-privilege cancel permission",
            "durable audited request, idempotence and cancellation result reconciliation",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Verify OC Fleet run status read-only")
    p.add_argument("--db", type=Path,
                   default=Path.home()/".local/share/ocfilo-telemetry/events.db")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--run")
    group.add_argument("--recent", type=int)
    p.add_argument("--attempt", default="1")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--propose-cancel", action="store_true")
    args = p.parse_args(argv)
    if args.recent is not None and (args.attempt != "1" or args.propose_cancel):
        p.error("--recent does not support --attempt or --propose-cancel")
    if type(args.limit) is not int or not 0 <= args.limit <= 50:
        p.error("invalid event limit")
    with read_only(args.db) as db:
        if args.run is not None:
            item = snapshot(db, args.run, args.attempt, args.limit)
            output = ({"schema_version": 1, "cancel_proposal": cancel_proposal(item)}
                      if args.propose_cancel else
                      {"schema_version": 1, "verified_run": item})
        else:
            items = recent(db, args.recent)
            # Limited sequential GitHub reads; never silently report unknown as running.
            output = {"schema_version": 1, "verified_runs": [
                snapshot(db, x["run_id"], x["attempt"], 0) for x in items]}
    print(json.dumps(output, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError,
            json.JSONDecodeError) as exc:
        # Never echo stderr from GitHub or DB values in a supervisor error.
        raise SystemExit("OC_FLEET_SUPERVISOR_BLOCKED: " + type(exc).__name__) from None
