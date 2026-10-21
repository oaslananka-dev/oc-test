#!/usr/bin/env python3
"""Offline audit of downloaded capacity-10 GitHub Actions artifacts.

No GitHub calls, execution, git mutation, model calls, publishing or PRs.
Only proves that an independently downloaded evidence bundle is internally
consistent with the registered plan and strict F2 scheduler. It cannot
authenticate the provenance of bytes independently of their download source,
nor override the GitHub workflow's actual conclusion.
"""
import argparse
import json
import re
import stat
from pathlib import Path

import ocfilo_f2_dag as dag
import ocfilo_f2_integrate as integrate
import ocfilo_f2_pilot as pilot
from ocfilo_f2_sched import load_evidence, schedule
from ocfilo_f2_strict_json import parse_strict_object

NAME = re.compile(r"ocfilo-f2-([1-9][0-9]*)-([1-9][0-9]*)-r([1-6])-([a-z0-9][a-z0-9-]*)\Z")
STATE_FIELDS = frozenset({
    "schema_version", "plan_id", "repository", "checkout_sha", "capacity",
    "max_attempts", "tasks", "dispatch", "outcome", "proposal_only",
})
MAX_STATE_BYTES = 16384


def _directory(path):
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError("Not a regular directory")
    except FileNotFoundError as exc:
        raise ValueError("Missing required artifact directory") from exc


def _regular(path, max_bytes):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
        raise ValueError("Unexpected file type or size")


def audit(root, run_id, run_attempt, sha):
    if (type(run_id) is not int or run_id < 1 or type(run_attempt) is not int
            or run_attempt < 1 or not isinstance(sha, str)
            or pilot.SHA.fullmatch(sha) is None):
        raise ValueError("Run identity and 40-hex SHA required")
    root = Path(root)
    _directory(root)
    raw = dag._plan("capacity-10")
    tasks = dag.read_dag(raw)
    expected = {tid: {key: task[key] for key in (
        "allowed_paths", "issue_number", "model", "identity")}
        for tid, task in tasks.items()}
    state_name = f"ocfilo-f2-dagstate-{run_id}-{run_attempt}"
    state_dir = root / state_name
    _directory(state_dir)
    state_files = list(state_dir.iterdir())
    if len(state_files) != 1 or state_files[0].name != "dag-state.json":
        raise ValueError("Unexpected state artifact contents")
    _regular(state_files[0], MAX_STATE_BYTES)
    recorded = parse_strict_object(state_files[0].read_text(encoding="utf-8"),
                                   STATE_FIELDS, max_chars=MAX_STATE_BYTES)
    if recorded.get("capacity") != 1 or recorded.get("max_attempts") != 2:
        raise ValueError("Unreviewed final-state scheduler settings")

    result = []
    seen = set()
    entries = list(root.iterdir())
    if len(entries) > 201:
        raise ValueError("Too many artifact directories")
    for directory in entries:
        if directory.name == state_name:
            continue
        match = NAME.fullmatch(directory.name)
        if not match or (int(match[1]), int(match[2])) != (run_id, run_attempt):
            raise ValueError("Unknown or unrelated artifact directory")
        round_no, tid = int(match[3]), match[4]
        if tid not in tasks or (round_no, tid) in seen:
            raise ValueError("Unexpected task or duplicate round")
        seen.add((round_no, tid))
        _directory(directory)
        files = {f.name: f for f in directory.iterdir()}
        if not set(files) <= {"result.json", "changes.patch"} or "result.json" not in files:
            raise ValueError("Unexpected worker artifact contents")
        for name, path in files.items():
            _regular(path, pilot.MAX_BYTES if name == "changes.patch" else 16384)
        items = load_evidence(directory)
        if len(items) != 1:
            raise ValueError("Expected one worker evidence item")
        ev = items[0]
        pilot.validate_evidence(ev, expected)
        if (ev["workflow_run_id"] != run_id
                or ev["run_attempt"] != run_attempt or ev["round"] != round_no
                or ev["task_id"] != tid or ev["checkout_sha"] != sha):
            raise ValueError("Artifact label and content provenance disagree")
        successful = ev["worker_outcome"] == "SUCCEEDED"
        if successful != ("changes.patch" in files):
            raise ValueError("Patch and worker outcome disagree")
        if successful:
            integrate.check_patch(files["changes.patch"].read_bytes(),
                                  tasks[tid]["allowed_paths"][0])
        result.append(ev)
    if not result:
        raise ValueError("Missing worker evidence")
    dag._require_native_run_evidence(result, {"run_id": run_id, "attempt": run_attempt})
    replayed = schedule(raw, result, sha, capacity=1)
    if replayed != recorded:
        raise ValueError("Downloaded DAG state disagrees with scheduler replay")

    succeeded = sorted(tid for tid, item in replayed["tasks"].items()
                       if item["state"] == "SUCCEEDED")
    failed_attempts = [ev for ev in result if ev["worker_outcome"] == "FAILED"]
    return {
        "run_id": run_id, "run_attempt": run_attempt, "checkout_sha": sha,
        "bundle_verified": True,
        "artifact_attempts": len(result),
        "patches_verified": sum(ev["worker_outcome"] == "SUCCEEDED" for ev in result),
        "outcome": replayed["outcome"],
        "succeeded_tasks": len(succeeded),
        "failed_attempts": len(failed_attempts),
        "recovered_tasks": sorted(set(ev["task_id"] for ev in failed_attempts)
                                  & set(succeeded)),
        "first_try_all_ten": len(succeeded) == 10 and len(result) == 10
                             and not failed_attempts and replayed["outcome"] == "COMPLETE",
        "github_workflow_conclusion_checked": False,
        "publishing_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", default=1, type=int)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        report = audit(args.artifacts, args.run_id, args.run_attempt, args.sha)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        parser.exit(2, "F2 capacity evidence audit refused (invalid bundle)\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
