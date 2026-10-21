#!/usr/bin/env python3
"""F2.3b offline DAG scheduler. Pure decision logic, no dispatch.

Input: a plan (validated by ocfilo_f2_plan), schema-2 worker evidence for one
exact checkout SHA, and the task IDs GitHub currently reports as running.
Output: per-task state and a bounded PROPOSAL of tasks to start next. It never
calls GitHub, starts workers, retries by itself or authorizes execution; a
human-initiated workflow still has to act on the proposal.
"""
import argparse
import json
from pathlib import Path

from ocfilo_f2_plan import plan as validate_plan, REPO
from ocfilo_f2_pilot import SHA, validate_evidence
from ocfilo_f2_strict_json import EVIDENCE_FIELDS, parse_strict_object

# Raise only after a measured 5-worker (then 10-worker) capacity run.
VALIDATED_CAPACITY = 5  # measured clean: runs 38359600536, 38359780386
HARD_CAPACITY = 10  # planner limit; a plan must register its own cap below this
MAX_ATTEMPT_BUDGET = 3
MAX_EVIDENCE = 200
MAX_EVIDENCE_BYTES = 16384
BAD = frozenset({"FAILED", "CANCELLED", "BLOCKED"})


def _bounded(value, high, label):
    if type(value) is not int or not 1 <= value <= high:
        raise ValueError(f"{label} must be an integer 1..{high}")


def _collect(raw_evidence, expected, sha):
    """Validate every item, drop exact duplicates, reject same-run conflicts."""
    if not isinstance(raw_evidence, list) or len(raw_evidence) > MAX_EVIDENCE:
        raise ValueError(f"Evidence must be a list of at most {MAX_EVIDENCE} items")
    seen = {}
    for item in raw_evidence:
        validate_evidence(item, expected)
        if item["checkout_sha"] != sha:
            raise ValueError("Evidence belongs to a different checkout SHA")
        key = (item["task_id"], item["workflow_run_id"], item["run_attempt"],
               item.get("round", 1))
        if key in seen and seen[key] != item:
            raise ValueError("Conflicting evidence for the same task run")
        seen[key] = item
    by_task = {}
    for (tid, *_), item in sorted(seen.items()):
        by_task.setdefault(tid, []).append(item)
    return by_task


def schedule(raw_plan, raw_evidence, sha, capacity=VALIDATED_CAPACITY,
             running=(), max_attempts=2, max_capacity=VALIDATED_CAPACITY):
    summary = validate_plan(raw_plan)
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("Exact 40-hex checkout SHA required")
    _bounded(max_capacity, HARD_CAPACITY, "max_capacity")
    _bounded(capacity, max_capacity, "capacity")
    _bounded(max_attempts, MAX_ATTEMPT_BUDGET, "max_attempts")
    order = [t for wave in summary["waves"] for t in wave["tasks"]]  # topological
    expected = {t["task_id"]: {
        "allowed_paths": t["allowed_paths"], "issue_number": t["issue_number"],
        "model": t["model"], "identity": t["identity"]} for t in order}
    running = set(running)
    if not running <= expected.keys():
        raise ValueError("Running task is not part of the plan")
    evidence = _collect(raw_evidence, expected, sha)

    tasks = {}
    for t in order:
        tid, deps = t["task_id"], t["depends_on"]
        runs = evidence.get(tid, [])
        executed = [e for e in runs if e["worker_outcome"] != "CONFIG_ONLY"]
        failed = [e for e in executed if e["worker_outcome"] != "SUCCEEDED"]
        entry = {"state": None, "attempts": len(failed), "last_failure_class": None,
                 "blocked_by": [d for d in deps if tasks[d]["state"] in BAD]}
        if failed:
            entry["last_failure_class"] = failed[-1]["failure_class"]
        if any(e["worker_outcome"] == "SUCCEEDED" for e in executed):
            if any(tasks[d]["state"] != "SUCCEEDED" for d in deps):
                raise ValueError(f"{tid}: success recorded before its dependencies")
            entry["state"] = "SUCCEEDED"
        elif entry["blocked_by"]:
            entry["state"] = "BLOCKED"
        elif any(tasks[d]["state"] != "SUCCEEDED" for d in deps):
            if tid in running:
                raise ValueError(f"{tid}: running before its dependencies")
            entry["state"] = "PENDING"
        elif tid in running:
            entry["state"] = "RUNNING"
        elif failed and failed[-1]["worker_outcome"] == "CANCELLED":
            entry["state"] = "CANCELLED"
        elif failed and (not failed[-1]["retryable"] or len(failed) >= max_attempts):
            entry["state"] = "FAILED"
        else:
            entry["state"] = "READY"
        tasks[tid] = entry

    active = sum(v["state"] == "RUNNING" for v in tasks.values())
    ready = [tid for tid, v in tasks.items() if v["state"] == "READY"]
    dispatch = ready[:max(0, capacity - active)]
    states = {v["state"] for v in tasks.values()}
    if states == {"SUCCEEDED"}:
        outcome = "COMPLETE"
    elif ready or "RUNNING" in states:
        outcome = "IN_PROGRESS"
    else:
        outcome = "HALTED"
    return {"schema_version": 1, "plan_id": summary["plan_id"], "repository": REPO,
            "checkout_sha": sha, "capacity": capacity, "max_attempts": max_attempts,
            "tasks": tasks, "dispatch": dispatch, "outcome": outcome,
            "proposal_only": True}


def load_evidence(directory):
    files = sorted(Path(directory).rglob("result.json"))
    if len(files) > MAX_EVIDENCE:
        raise ValueError("Too many evidence files")
    out = []
    for f in files:
        if f.is_symlink() or f.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError("Unsafe or oversized evidence file")
        out.append(parse_strict_object(
            f.read_text(encoding="utf-8"), EVIDENCE_FIELDS,
            max_chars=MAX_EVIDENCE_BYTES))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--capacity", type=int, default=VALIDATED_CAPACITY)
    ap.add_argument("--running", default="", help="comma-separated task IDs")
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)
    try:
        source = Path(args.plan)
        if source.stat().st_size > 64000:
            raise ValueError("Plan exceeds 64 KB")
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
        running = [x for x in args.running.split(",") if x]
        result = schedule(raw, load_evidence(args.evidence_dir), args.sha,
                          args.capacity, running)
        with Path(args.output).open("x", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        ap.exit(2, f"F2 scheduler rejected: {exc}\n")
    print(json.dumps({"plan_id": result["plan_id"], "outcome": result["outcome"],
                      "dispatch": result["dispatch"], "proposal_only": True}))


if __name__ == "__main__":
    main()
