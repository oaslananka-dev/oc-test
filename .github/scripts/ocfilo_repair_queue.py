#!/usr/bin/env python3
"""ocfilo F1 durable, single-writer, SHA-bound repair reservation queue.

No networking, GitHub permissions, model invocation or workflow dispatch here.
The authorized manager alone may submit a live decision and acknowledge a run.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
SHA = re.compile(r"[0-9a-f]{40}\Z")
KEY = re.compile(r"[0-9a-f]{20}\Z")
DELIVERY = re.compile(r"[A-Za-z0-9._:-]{5,120}\Z")
MAX_REPAIRS = 3
ACTIVE = ("RESERVED", "DISPATCHED")
FINAL = ("SUCCEEDED", "FAILED", "CANCELLED")


def connect(path):
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(database), timeout=10, isolation_level=None)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS repair_jobs(
          id INTEGER PRIMARY KEY,
          repository TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          head_sha TEXT NOT NULL,
          fingerprint TEXT NOT NULL,
          delivery_id TEXT NOT NULL UNIQUE,
          attempt INTEGER NOT NULL,
          state TEXT NOT NULL CHECK(state IN
               ('RESERVED','DISPATCHED','SUCCEEDED','FAILED','CANCELLED')),
          workflow_run_id INTEGER UNIQUE,
          created_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          updated_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ocfilo_repair_scope
          ON repair_jobs(repository,pr_number,id);
    """)
    return con


def format_row(row):
    if row is None:
        return None
    fields = ["id", "repository", "pr_number", "head_sha", "fingerprint",
              "delivery_id", "attempt", "state", "workflow_run_id",
              "created_utc", "updated_utc"]
    return dict(zip(fields, row))


def fetch(con, ident):
    return format_row(con.execute(
        """SELECT id,repository,pr_number,head_sha,fingerprint,
                  delivery_id,attempt,state,workflow_run_id,created_utc,updated_utc
           FROM repair_jobs WHERE id=?""", (ident,)).fetchone())


def _check_decision(decision, head_sha, delivery_id):
    if not isinstance(decision, dict):
        raise ValueError("Expected decision object")
    if decision.get("repository") != REPO:
        raise ValueError("Unexpected repository")
    if type(decision.get("pr_number")) is not int or decision["pr_number"] != 1:
        raise ValueError("F1 queue is restricted to PR #1")
    if decision.get("state") != "REPAIR_REQUIRED" or decision.get("dispatch_allowed") is not True:
        raise ValueError("Not a dispatchable repair decision")
    if not SHA.fullmatch(str(head_sha)) or decision.get("head_sha") != head_sha:
        raise ValueError("Live head SHA does not match decision")
    if not KEY.fullmatch(str(decision.get("repair_key", ""))):
        raise ValueError("Missing SHA-bound fingerprint")
    if not DELIVERY.fullmatch(str(delivery_id)):
        raise ValueError("Invalid delivery id")
    return decision


def reserve(con, decision, current_head_sha, delivery_id):
    """Atomically reserve at most one active writer per PR.

    There is deliberately NO expiry/auto-retry on an ambiguous reservation.
    An operator must reconcile an interrupted dispatch with GitHub first.
    """
    d = _check_decision(decision, current_head_sha, delivery_id)
    con.execute("BEGIN IMMEDIATE")
    try:
        existing = con.execute(
            "SELECT id,head_sha,fingerprint FROM repair_jobs WHERE delivery_id=?",
            (delivery_id,)).fetchone()
        if existing:
            if existing[1:] != (current_head_sha, d["repair_key"]):
                raise ValueError("Reused delivery ID with different task")
            result = {"outcome": "DUPLICATE_DELIVERY", "job": fetch(con, existing[0])}
            con.commit()
            return result
        active = con.execute(
            """SELECT id FROM repair_jobs
               WHERE repository=? AND pr_number=? AND state IN ('RESERVED','DISPATCHED')
               ORDER BY id DESC LIMIT 1""", (REPO, d["pr_number"])).fetchone()
        if active:
            result = {"outcome": "ALREADY_ACTIVE", "job": fetch(con, active[0])}
            con.commit()
            return result
        history = con.execute(
            "SELECT COUNT(*) FROM repair_jobs WHERE repository=? AND pr_number=?",
            (REPO, d["pr_number"])).fetchone()[0]
        if history >= MAX_REPAIRS:
            con.commit()
            return {"outcome": "NEEDS_HUMAN", "job": None}
        prior = con.execute(
            """SELECT id,state FROM repair_jobs
               WHERE repository=? AND pr_number=? AND head_sha=? AND fingerprint=?
               ORDER BY id DESC LIMIT 1""",
            (REPO, d["pr_number"], current_head_sha, d["repair_key"])).fetchone()
        if prior and prior[1] == "SUCCEEDED":
            con.commit()
            return {"outcome": "ALREADY_REPAIRED", "job": fetch(con, prior[0])}
        cur = con.execute(
            """INSERT INTO repair_jobs
               (repository,pr_number,head_sha,fingerprint,delivery_id,attempt,state)
               VALUES(?,?,?,?,?,?, 'RESERVED')""",
            (REPO, d["pr_number"], current_head_sha, d["repair_key"],
             delivery_id, history + 1))
        result = {"outcome": "RESERVED", "job": fetch(con, cur.lastrowid)}
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def acknowledge(con, job_id, run_id, expected_sha):
    """Only call after verifying GitHub accepted a matching workflow dispatch."""
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Invalid workflow run id")
    if not SHA.fullmatch(str(expected_sha)):
        raise ValueError("Expected SHA required")
    con.execute("BEGIN IMMEDIATE")
    try:
        job = fetch(con, job_id)
        if job is None or job["head_sha"] != expected_sha or job["state"] != "RESERVED":
            raise ValueError("Reservation missing, stale or already acknowledged")
        con.execute(
            """UPDATE repair_jobs SET state='DISPATCHED',workflow_run_id=?,
               updated_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (run_id, job_id))
        result = fetch(con, job_id)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def finalize(con, job_id, run_id, expected_sha, outcome):
    """Record independently verified terminal workflow status only."""
    if outcome not in FINAL:
        raise ValueError("Invalid final outcome")
    if type(run_id) is not int or run_id <= 0 or not SHA.fullmatch(str(expected_sha)):
        raise ValueError("Invalid run id or SHA")
    con.execute("BEGIN IMMEDIATE")
    try:
        job = fetch(con, job_id)
        if (job is None or job["state"] != "DISPATCHED"
                or job["workflow_run_id"] != run_id or job["head_sha"] != expected_sha):
            raise ValueError("Run identity/state/SHA mismatch")
        con.execute(
            """UPDATE repair_jobs SET state=?,
               updated_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (outcome, job_id))
        result = fetch(con, job_id)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    sub = ap.add_subparsers(dest="op", required=True)
    p = sub.add_parser("reserve")
    p.add_argument("--decision", required=True)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--delivery-id", required=True)
    for op in ("ack", "finalize"):
        subp = sub.add_parser(op)
        subp.add_argument("--job-id", type=int, required=True)
        subp.add_argument("--run-id", type=int, required=True)
        subp.add_argument("--head-sha", required=True)
        if op == "finalize":
            subp.add_argument("--outcome", required=True, choices=FINAL)
    sub.add_parser("list")
    args = ap.parse_args()
    con = connect(args.db)
    try:
        if args.op == "reserve":
            d = json.loads(Path(args.decision).read_text(encoding="utf-8-sig"))
            result = reserve(con, d, args.head_sha, args.delivery_id)
        elif args.op == "ack":
            result = acknowledge(con, args.job_id, args.run_id, args.head_sha)
        elif args.op == "finalize":
            result = finalize(con, args.job_id, args.run_id, args.head_sha, args.outcome)
        else:
            ids = con.execute("SELECT id FROM repair_jobs ORDER BY id").fetchall()
            result = [fetch(con, x[0]) for x in ids]
        print(json.dumps(result, indent=2))
    finally:
        con.close()


if __name__ == "__main__":
    main()
