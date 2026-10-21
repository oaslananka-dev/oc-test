#!/usr/bin/env python3
"""Local, read-only supervisor observations of verified OC Fleet telemetry.

For use by an authorized operator/agent through an existing device connection.
Not an HTTP API, OpenCode session, command channel or GitHub state oracle.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from oc_fleet_telemetry_bridge import validate_event

RUN_ID = re.compile(r"[0-9]{1,20}\Z")
ATTEMPT = re.compile(r"[1-9][0-9]{0,4}\Z")
ISSUE = re.compile(r"[A-Z][A-Z0-9]{1,14}-[1-9][0-9]{0,9}\Z")
MAX_EVENTS = 10000
METRICS = ("tokens_input", "tokens_output", "tokens_reasoning",
           "cache_read", "cache_write", "cost_usd")


def utc(value):
    return (datetime.fromtimestamp(value, timezone.utc)
            .isoformat().replace("+00:00", "Z") if type(value) is int and value > 0
            else None)


@contextmanager
def read_only(path):
    """A missing DB must not be created. No schema migration or write privilege."""
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("telemetry database is not a file")
    uri = "file:" + quote(str(resolved), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=3)
    try:
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


def observation(db, run, attempt, limit=20):
    """Return bounded, allowlisted fields. No original JSONL, prompt or tool args."""
    if (not isinstance(run, str) or not RUN_ID.fullmatch(run)
            or not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt)
            or type(limit) is not int or not 0 <= limit <= 50):
        raise ValueError("invalid observation request")
    binding = db.execute(
        "SELECT issue,created,updated,reported_seq,comment_id FROM binding "
        "WHERE run=? AND attempt=?", (run, attempt)).fetchone()
    if binding is None:
        raise ValueError("unregistered telemetry run")
    issue, created, updated, reported, comment = binding
    if not isinstance(issue, str) or not ISSUE.fullmatch(issue):
        raise ValueError("invalid registered issue")
    if type(reported) is not int or reported < 0:
        raise ValueError("invalid progress sequence")
    rows = db.execute(
        "SELECT seq,kind,tool,status,metrics FROM event "
        "WHERE run=? AND attempt=? ORDER BY seq LIMIT ?",
        (run, attempt, MAX_EVENTS + 1)).fetchall()
    if len(rows) > MAX_EVENTS:
        raise ValueError("unbounded event history")
    recent = []
    usage = {key: 0 for key in METRICS}
    behind = 0
    last_seq = 0
    for seq, kind, tool, status, payload in rows:
        # Fail closed if the local DB has been changed outside the signed
        # receiver. Its content is never used directly as a command.
        metric = json.loads(payload)
        if not isinstance(metric, dict) or not set(metric) <= set(METRICS):
            raise ValueError("invalid stored metrics")
        event = {"run_id": run, "attempt": attempt, "seq": seq, "kind": kind,
                 "tool": tool, "status": status, **metric}
        validate_event(event, run, attempt)
        if seq <= last_seq:
            raise ValueError("non-monotonic event history")
        last_seq = seq
        if seq > reported:
            behind += 1
        if kind == "step_finish":
            for k in METRICS:
                usage[k] += metric.get(k, 0)
        if limit:
            record = {"sequence": seq, "type": kind}
            if kind == "tool_use":
                record.update(tool=tool or "other", status=status or "unknown")
            if kind == "step_finish":
                record["usage"] = {k: metric[k] for k in METRICS if k in metric}
            recent.append(record)
            if len(recent) > limit:
                recent.pop(0)
    phases = {"step_start": "step_started", "step_finish": "step_finished",
              "tool_use": "tool_observed", "error": "error_observed"}
    latest_type = recent[-1]["type"] if recent else None
    if rows and not recent:
        latest_type = rows[-1][1]
    return {
        "run_id": run,
        "attempt": attempt,
        "multica_issue": issue,
        "registered_at_utc": utc(created),
        "multica_comment_updated_at_utc": utc(updated),
        "multica_comment_exists": bool(comment),
        "event_count": len(rows),
        "latest_sequence": last_seq,
        "events_not_yet_reported_to_multica": behind,
        "last_observed_phase": phases.get(latest_type, "no_events_yet"),
        "reported_usage_totals": usage,
        "recent_safe_events": recent,
        "github_run_state": "not_checked",
        "note": "Telemetry events are not proof of live execution or completion; verify GitHub Actions run separately.",
    }


def recent(db, count=10):
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError("invalid recent limit")
    runs = db.execute(
        "SELECT run,attempt FROM binding ORDER BY created DESC,run DESC LIMIT ?",
        (count,)).fetchall()
    return [observation(db, run, attempt, 0) for run, attempt in runs]


def main(argv=None):
    p = argparse.ArgumentParser(description="Read-only trusted OC Fleet telemetry")
    p.add_argument("--db", type=Path,
                   default=Path.home()/".local/share/ocfilo-telemetry/events.db")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", help="GitHub Actions numeric run ID")
    group.add_argument("--recent", type=int, help="Recent registered runs (1..20)")
    p.add_argument("--attempt", default="1")
    p.add_argument("--limit", type=int, default=20, help="Last N safe events, 0..50")
    args = p.parse_args(argv)
    if type(args.limit) is not int or not 0 <= args.limit <= 50:
        p.error("invalid event limit")
    if args.run is None and args.attempt != "1":
        p.error("--attempt requires --run")
    with read_only(args.db) as db:
        data = ({"schema_version": 1, "source": "local-readonly-verified-telemetry",
                 "runs": recent(db, args.recent)} if args.run is None
                else {"schema_version": 1, "source": "local-readonly-verified-telemetry",
                      "run": observation(db, args.run, args.attempt, args.limit)})
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        # Never echo raw DB values, credentials or exception text to an agent.
        raise SystemExit("OC_FLEET_OBSERVE_BLOCKED: " + type(exc).__name__) from None
