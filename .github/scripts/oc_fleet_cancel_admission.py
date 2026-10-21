#!/usr/bin/env python3
"""Offline-only, audited admission contract for a future OC Fleet cancel executor.

Not a cancellation client. No CLI, network mutator, credential loader,
approved identity provider, or production transport is provided by this module.
Trusted receipt and approval verifiers MUST be added by a separately reviewed
control plane before any live cancellation can be supported.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import os
import stat

from oc_fleet_supervisor import REPO, WORKFLOW, SHA, verified_state

ID = re.compile(r"[0-9a-f]{64}\Z")
TASK = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
ACTOR = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
ISSUE = re.compile(r"[A-Z][A-Z0-9]{1,14}-[1-9][0-9]{0,9}\Z")
UTC = "%Y-%m-%dT%H:%M:%SZ"
KEYS = ("action", "repository", "run_id", "run_attempt", "head_sha",
        "workflow", "registered_multica_issue")


def canonical(fields):
    return json.dumps(fields, separators=(",", ":"), sort_keys=True)


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise ValueError("invalid UTC timestamp")
    return datetime.strptime(value, UTC).replace(tzinfo=timezone.utc)


def validate_proposal(proposal, now):
    if not isinstance(proposal, dict) or not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("invalid review inputs")
    if (proposal.get("schema_version") != 1
            or proposal.get("executable") is not False
            or proposal.get("approval") != "required_not_granted"
            or proposal.get("manifest_task_identity") != "not_verified"):
        raise ValueError("unapproved proposal format")
    fields = {key: proposal.get(key) for key in KEYS}
    if (fields["action"] != "propose_cancel" or fields["repository"] != REPO
            or fields["workflow"] != WORKFLOW
            or not isinstance(fields["run_id"], str)
            or not re.fullmatch(r"[0-9]{1,20}", fields["run_id"])
            or not isinstance(fields["run_attempt"], str)
            or not re.fullmatch(r"[1-9][0-9]{0,4}", fields["run_attempt"])
            or not isinstance(fields["head_sha"], str)
            or not SHA.fullmatch(fields["head_sha"])
            or not isinstance(fields["registered_multica_issue"], str)
            or not ISSUE.fullmatch(fields["registered_multica_issue"])):
        raise ValueError("untrusted proposed run")
    proposal_id = hashlib.sha256(canonical(fields).encode("utf-8")).hexdigest()
    if (not isinstance(proposal.get("proposal_id"), str)
            or not ID.fullmatch(proposal["proposal_id"])
            or proposal["proposal_id"] != proposal_id):
        raise ValueError("tampered proposal identity")
    issued = timestamp(proposal.get("requested_at_utc"))
    deadline = timestamp(proposal.get("expires_at_utc"))
    now = now.astimezone(timezone.utc)
    if (issued > now + timedelta(seconds=15)
            or now >= deadline or deadline <= issued
            or deadline - issued > timedelta(minutes=5)):
        raise ValueError("proposal expired or invalid")
    return fields


def matching_fields(fields, evidence):
    if not isinstance(evidence, dict):
        raise ValueError("untrusted evidence")
    for k, value in (
        ("repository", fields["repository"]),
        ("run_id", fields["run_id"]),
        ("attempt", fields["run_attempt"]),
        ("head_sha", fields["head_sha"]),
        ("multica_issue", fields["registered_multica_issue"]),
    ):
        if type(evidence.get(k)) is not str or evidence[k] != value:
            raise ValueError("receipt/run mismatch")
    task = evidence.get("task_id")
    if not isinstance(task, str) or not TASK.fullmatch(task):
        raise ValueError("no verified task identity")
    return task


def admit_offline(proposal, *, github_reader, receipt_verifier,
                  approval_verifier, audit_db, now=None):
    """Simulate gate+audit only. Never dispatch a cancel request.

    All three callbacks represent separately authenticated *trusted* boundary
    adapters, deliberately NOT implemented in this repository. Providing a
    lambda/mock is suitable only in offline tests and is NOT a real approval.
    """
    if not callable(github_reader) or not callable(receipt_verifier) or not callable(approval_verifier):
        raise ValueError("trusted verification adapters missing")
    now = now or datetime.now(timezone.utc)
    fields = validate_proposal(proposal, now)
    # A fresh GH read is required even if the proposal was previously valid.
    run = github_reader(fields["run_id"])
    state = verified_state(fields["run_id"], fields["run_attempt"], run)
    if (state["state"] != "nonterminal_at_github"
            or state["head_sha"] != fields["head_sha"]):
        raise ValueError("run no longer eligible or head SHA mismatch")
    task = matching_fields(fields, receipt_verifier(fields))
    approval = approval_verifier(fields, task)
    if not isinstance(approval, dict) or approval.get("approved") is not True:
        raise ValueError("human approval not granted")
    if (approval.get("proposal_id") != proposal["proposal_id"]
            or approval.get("head_sha") != fields["head_sha"]
            or approval.get("run_id") != fields["run_id"]
            or approval.get("attempt") != fields["run_attempt"]
            or approval.get("task_id") != task):
        raise ValueError("approval does not match task/run")
    actor = approval.get("actor")
    if type(actor) is not str or not ACTOR.fullmatch(actor):
        raise ValueError("unverified approver")
    if not isinstance(audit_db, (str, Path)):
        raise ValueError("missing audit destination")
    audit = Path(audit_db).expanduser()
    # This simulator writes only the caller-chosen audit file; it must NEVER
    # reuse the separate signed telemetry events database.
    if audit.name == "events.db" or audit.is_symlink():
        raise ValueError("audit destination is not isolated")
    if not audit.parent.exists() or not audit.parent.is_dir():
        raise ValueError("audit directory unavailable")
    if audit.exists():
        st = audit.stat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o077):
            raise ValueError("audit destination ownership or permissions unsafe")
    old_umask = os.umask(0o077)
    try:
        db = sqlite3.connect(audit, timeout=5)
    finally:
        os.umask(old_umask)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("CREATE TABLE IF NOT EXISTS admission "
                   "(proposal_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,"
                   "attempt TEXT NOT NULL, head_sha TEXT NOT NULL,"
                   "task_id TEXT NOT NULL, actor TEXT NOT NULL,"
                   "created_utc TEXT NOT NULL, status TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        before = db.execute(
            "SELECT run_id,attempt,head_sha,task_id,actor,status FROM admission "
            "WHERE proposal_id=?", (proposal["proposal_id"],)).fetchone()
        if before:
            if before[:5] != (fields["run_id"], fields["run_attempt"],
                              fields["head_sha"], task, actor):
                raise ValueError("proposal ID reused with different evidence")
            db.commit()
            status = "duplicate_recorded_offline"
        else:
            db.execute(
                "INSERT INTO admission VALUES (?,?,?,?,?,?,?,?)",
                (proposal["proposal_id"], fields["run_id"], fields["run_attempt"],
                 fields["head_sha"], task, actor, now.astimezone(timezone.utc)
                 .strftime(UTC), "simulation_only_no_cancel"))
            db.commit()
            status = "recorded_offline_only"
        return {"proposal_id": proposal["proposal_id"], "run_id": fields["run_id"],
                "attempt": fields["run_attempt"], "task_id": task,
                "result": status, "cancel_requested": False,
                "execution_enabled": False}
    finally:
        db.close()
