#!/usr/bin/env python3
"""ocfilo F1 read-only decision handoff verification.

Verifies that a GitHub Actions observer artifact matches an authenticated run
and a separately fetched *current* PR decision. Produces no action commands.
Durable SQLite delivery acknowledgements prevent re-processing the same run/PR.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
ALLOWED_PRS = frozenset({1, 2})
OBSERVER = ".github/workflows/ocfilo-pr-observer.yml"
VALID_EVENTS = frozenset({"workflow_run", "check_run", "pull_request", "workflow_dispatch"})
SHA = re.compile(r"[0-9a-f]{40}\Z")
KEY = re.compile(r"[0-9a-f]{20}\Z")
OBSERVED_FIELDS = frozenset({
    "repository", "pr_number", "head_sha", "state", "dispatch_allowed",
    "repair_key", "failing_checks", "pending_checks", "missing_checks",
    "blocking_reviews", "bot_feedback",
})
VALID_STATES = frozenset({
    "WAITING_CHECKS", "REPAIR_REQUIRED", "WAITING_REPAIR",
    "NEEDS_HUMAN", "REVIEW_BLOCKED", "BOT_FEEDBACK_TRIAGE",
    "READY_FOR_REVIEW", "MERGE_BLOCKED", "REVIEW_PENDING",
    "READY_FOR_MERGE", "CLOSED", "STALE_EVENT", "STALE_SNAPSHOT",
})


def validate(observer, current, run, artifact, pr_number):
    if type(pr_number) is not int or pr_number not in ALLOWED_PRS:
        raise ValueError("PR is not in the F1 allowlist")
    if not all(isinstance(v, dict) for v in (observer, current, run, artifact)):
        raise ValueError("Inputs must be JSON objects")
    if (run.get("repository", {}).get("full_name") != REPO
        or run.get("path") != OBSERVER
        or run.get("event") not in VALID_EVENTS
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or type(run.get("id")) is not int
        or run["id"] < 1):
        raise ValueError("Untrusted or unfinished observer workflow")
    expected_name = f"ocfilo-pr-observer-{run['id']}-{pr_number}"
    if (artifact.get("name") != expected_name
        or artifact.get("expired") is not False
        or type(artifact.get("workflow_run", {}).get("id")) is not int
        or artifact["workflow_run"]["id"] != run["id"]):
        raise ValueError("Artifact name/run provenance mismatch")
    if observer.get("repository") != REPO or current.get("repository") != REPO:
        raise ValueError("Decision repo mismatch")
    if observer.get("pr_number") != pr_number or current.get("pr_number") != pr_number:
        raise ValueError("Decision PR mismatch")
    sha = observer.get("head_sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha) or current.get("head_sha") != sha:
        raise ValueError("Observer decision stale relative to current PR HEAD")
    for label in ("state", "dispatch_allowed", "repair_key"):
        if observer.get(label) != current.get(label):
            raise ValueError(f"Observer decision diverges from live {label}")
    for label in ("failing_checks", "pending_checks", "missing_checks", "blocking_reviews", "bot_feedback"):
        left = observer.get(label)
        right = current.get(label)
        if not isinstance(left, list) or not isinstance(right, list) or left != right:
            raise ValueError(f"Observer decision diverges from live {label}")
    if observer.get("state") not in VALID_STATES:
        raise ValueError("Unrecognized PR decision state")
    if type(observer.get("dispatch_allowed")) is not bool:
        raise ValueError("Invalid dispatch boolean")
    if observer["dispatch_allowed"]:
        if (observer["state"] != "REPAIR_REQUIRED"
            or not KEY.fullmatch(str(observer.get("repair_key", "")))
            or not observer["failing_checks"]
            or observer["pending_checks"] or observer["missing_checks"]):
            raise ValueError("Unsafe dispatch recommendation")
    return {
        "repository": REPO,
        "observer_run_id": run["id"],
        "artifact_name": expected_name,
        "pr_number": pr_number,
        "head_sha": sha,
        "state": observer["state"],
        "dispatch_allowed": observer["dispatch_allowed"],
        "repair_key": observer.get("repair_key"),
        "outcome": "VERIFIED_READ_ONLY",
    }


def remember(db_path, result):
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(database), isolation_level=None, timeout=10)
    try:
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("BEGIN IMMEDIATE")
        con.execute("""CREATE TABLE IF NOT EXISTS observer_deliveries(
            observer_run_id INTEGER NOT NULL,
            pr_number INTEGER NOT NULL,
            head_sha TEXT NOT NULL,
            state TEXT NOT NULL,
            dispatch_allowed INTEGER NOT NULL,
            repair_key TEXT,
            verified_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(observer_run_id,pr_number)
        )""")
        existing = con.execute(
            """SELECT head_sha,state,dispatch_allowed,repair_key FROM observer_deliveries
               WHERE observer_run_id=? AND pr_number=?""",
            (result["observer_run_id"],result["pr_number"])).fetchone()
        values = (result["head_sha"],result["state"],
                  int(result["dispatch_allowed"]),result["repair_key"])
        if existing:
            if existing != values:
                raise ValueError("Conflicting replay for same observer run and PR")
            result = dict(result, outcome="DUPLICATE_VERIFIED_DELIVERY")
        else:
            con.execute(
                """INSERT INTO observer_deliveries(
                  observer_run_id,pr_number,head_sha,state,dispatch_allowed,repair_key)
                  VALUES(?,?,?,?,?,?)""",
                (result["observer_run_id"], result["pr_number"], *values))
        con.commit()
        return result
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    for name in ("observer", "current", "run", "artifact", "db", "output"):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--pr", type=int, required=True)
    args = ap.parse_args()
    data = {}
    for name in ("observer", "current", "run", "artifact"):
        data[name] = json.loads(Path(getattr(args,name)).read_text(encoding="utf-8-sig"))
    result = validate(data["observer"],data["current"],data["run"],data["artifact"],args.pr)
    result = remember(args.db, result)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
