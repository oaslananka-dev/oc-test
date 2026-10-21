#!/usr/bin/env python3
"""OFFLINE cancel-result state machine on isolated simulation audit records.

This module has NO GitHub write API, network client, CLI, or real cancel entry.
Test-supplied response codes are simulation fixtures, never evidence that any
production worker received a cancellation request. A separate owner-approved,
audited executor and trusted identity providers remain prerequisites.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat

from oc_fleet_supervisor import verified_state

PROPOSAL = re.compile(r"[0-9a-f]{64}\Z")
STATES = {"outcome_unknown", "accepted_unconfirmed", "conflict_unresolved",
          "permission_denied", "terminal_cancelled_observed",
          "terminal_other_observed"}
TERMINAL = {"terminal_cancelled_observed", "terminal_other_observed"}
CREATE_SIM = (
    "CREATE TABLE IF NOT EXISTS cancel_simulation("
    "proposal_id TEXT PRIMARY KEY, state TEXT NOT NULL, http_status INTEGER,"
    " FOREIGN KEY(proposal_id) REFERENCES admission(proposal_id))"
)
CREATE_EVENTS = (
    "CREATE TABLE IF NOT EXISTS cancel_simulation_event("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,"
    " event TEXT NOT NULL, resulting_state TEXT NOT NULL,"
    " recorded_at_utc TEXT NOT NULL)"
)


@contextmanager
def transaction(audit_path):
    """Require a preexisting private offline admission DB; never create one."""
    if not isinstance(audit_path, (str, Path)):
        raise ValueError("audit path required")
    path = Path(audit_path).expanduser()
    if path.name == "events.db" or path.is_symlink():
        raise ValueError("isolated audit required")
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise ValueError("untrusted audit permissions")
    # URI rw prevents silently creating a new simulation DB.
    from urllib.parse import quote
    db = sqlite3.connect(
        "file:" + quote(str(path.resolve(strict=True)), safe="/") + "?mode=rw",
        uri=True, timeout=5)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute(CREATE_SIM)
        db.execute(CREATE_EVENTS)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def admitted(db, proposal_id):
    if not isinstance(proposal_id, str) or not PROPOSAL.fullmatch(proposal_id):
        raise ValueError("invalid proposal identity")
    row = db.execute(
        "SELECT run_id,attempt,head_sha,status FROM admission "
        "WHERE proposal_id=?", (proposal_id,)).fetchone()
    if row is None or row[3] != "simulation_only_no_cancel":
        raise ValueError("missing offline admission receipt")
    return row[:3]


def state(db, proposal_id):
    row = db.execute(
        "SELECT state,http_status FROM cancel_simulation WHERE proposal_id=?",
        (proposal_id,)).fetchone()
    if row and row[0] not in STATES:
        raise ValueError("corrupted lifecycle state")
    return row


def log(db, proposal_id, event, result):
    db.execute(
        "INSERT INTO cancel_simulation_event"
        "(proposal_id,event,resulting_state,recorded_at_utc)"
        " VALUES(?,?,?,?)",
        (proposal_id, event, result, datetime.now(timezone.utc)
         .isoformat(timespec="seconds").replace("+00:00", "Z")))


def record_simulated_intent(audit_path, proposal_id):
    """Persistent *mock* pre-send marker. Duplicate never queues another send."""
    with transaction(audit_path) as db:
        admitted(db, proposal_id)
        existing = state(db, proposal_id)
        if existing:
            return receipt(proposal_id, existing[0], "repeat_suppressed")
        db.execute(
            "INSERT INTO cancel_simulation VALUES(?,?,NULL)",
            (proposal_id, "outcome_unknown"))
        log(db, proposal_id, "simulation_intent_only", "outcome_unknown")
        return receipt(proposal_id, "outcome_unknown", "simulation_intent_recorded")


def record_simulated_http(audit_path, proposal_id, http_status):
    """Accept synthetic GitHub response ONLY, never send any HTTP request.

    202 accepted != cancelled; 409 may be a race. No automatic retry on
    missing/timeout response, 5xx or any other ambiguous outcome.
    """
    category = {202: "accepted_unconfirmed",
                409: "conflict_unresolved",
                401: "permission_denied",
                403: "permission_denied",
                None: "outcome_unknown",
                500: "outcome_unknown",
                502: "outcome_unknown",
                503: "outcome_unknown",
                504: "outcome_unknown"}
    if type(http_status) is not int and http_status is not None:
        raise ValueError("invalid response type")
    if http_status not in category:
        raise ValueError("unsupported simulated HTTP outcome")
    target = category[http_status]
    with transaction(audit_path) as db:
        admitted(db, proposal_id)
        previous = state(db, proposal_id)
        if previous is None:
            raise ValueError("no prior simulated intent")
        old, seen_http = previous
        if old in TERMINAL:
            raise ValueError("terminal observation cannot be overwritten")
        if seen_http is not None:
            if seen_http == http_status:
                return receipt(proposal_id, old, "duplicate_response")
            raise ValueError("contradictory simulated HTTP result")
        if old != "outcome_unknown":
            raise ValueError("unexpected prior simulation state")
        # A transport timeout remains unknown. Never re-send automatically.
        if http_status is not None:
            db.execute(
                "UPDATE cancel_simulation SET state=?,http_status=? "
                "WHERE proposal_id=?", (target, http_status, proposal_id))
            log(db, proposal_id, "mock_http_" + str(http_status), target)
            return receipt(proposal_id, target, "simulated_response_recorded")
        return receipt(proposal_id, "outcome_unknown", "uncertain_no_auto_retry")


def reconcile_simulated(audit_path, proposal_id, github_response):
    """Only validated GitHub terminal state proves termination, never HTTP 202."""
    with transaction(audit_path) as db:
        run, attempt, sha = admitted(db, proposal_id)
        previous = state(db, proposal_id)
        if previous is None:
            raise ValueError("no simulated intent")
        verified = verified_state(run, attempt, github_response)
        if verified["head_sha"] != sha:
            raise ValueError("run SHA changed during reconciliation")
        old = previous[0]
        if verified["state"] == "nonterminal_at_github":
            if old in TERMINAL:
                raise ValueError("terminal run cannot become nonterminal")
            return receipt(proposal_id, old, "still_nonterminal_no_retry")
        terminal = ("terminal_cancelled_observed"
                    if verified["github_conclusion"] == "cancelled"
                    else "terminal_other_observed")
        if old in TERMINAL:
            if old != terminal:
                raise ValueError("contradictory terminal observation")
            return receipt(proposal_id, old, "terminal_already_recorded")
        db.execute(
            "UPDATE cancel_simulation SET state=? WHERE proposal_id=?",
            (terminal, proposal_id))
        log(db, proposal_id, "github_terminal_" +
            ("cancelled" if terminal == "terminal_cancelled_observed" else "other"),
            terminal)
        return receipt(proposal_id, terminal, "github_terminal_observed")


def receipt(proposal_id, status, result):
    return {"proposal_id": proposal_id,
            "state": status,
            "result": result,
            "simulator_only": True,
            "cancel_request_sent": False,
            "no_automatic_retry": True,
            "cancellation_attribution": "not_proven"}
