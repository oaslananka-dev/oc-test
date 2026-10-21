#!/usr/bin/env python3
"""F2.3c manual multi-round DAG runner glue. No GitHub writes, no dispatch.

A trusted, human-started workflow calls this once per round: it asks the F2.3b
scheduler what may run, emits a bounded matrix, and later prepares/finalizes
each task with the same fail-closed scope and evidence rules as the F2.2 pilot.
Only reviewed manifests registered in DAG_PLANS are accepted.
"""
import argparse
import json
import os
from pathlib import Path

import ocfilo_f2_pilot as pilot
from ocfilo_f2_plan import plan as validate_plan
from ocfilo_f2_sched import (MAX_EVIDENCE, load_evidence, schedule,
                             VALIDATED_CAPACITY)

# name -> (manifest, plan_id, max capacity). Raise a cap only after a measured run
# at that width; capacity-5 exists to take that measurement.
DAG_PLANS = {
    "dag-pilot": ("examples/ocfilo-f2-dag-pilot.json", "f2-dag", VALIDATED_CAPACITY),
    "capacity-5": ("examples/ocfilo-f2-capacity-5.json", "f2-cap5", 5),
    "code-5": ("examples/ocfilo-f2-code-5.json", "f2-code5", 5),
    "capacity-10": ("examples/ocfilo-f2-capacity-10.json", "f2-cap10", 10),
}
CAPS = {plan_id: cap for _, plan_id, cap in DAG_PLANS.values()}
DAG_PREFIX = "playground/f2/dag/"


def read_dag(raw, plan_id=None):
    """Validate a reviewed manifest; return {task_id: manifest item}."""
    validate_plan(raw)
    if raw["plan_id"] not in CAPS or (plan_id is not None and raw["plan_id"] != plan_id):
        raise ValueError("Manifest plan_id does not match a registered plan")
    tasks = {}
    for item in raw["tasks"]:
        paths = item["allowed_paths"]
        issue_bound_to_plan = 115 if raw["plan_id"] == "f2-code5" else 10
        if (item.get("issue_number") != issue_bound_to_plan
                or item["model"] != pilot.MODEL
                or item["agent"] != "build"
                or item["profile"] != "base"
                or item["identity"] != "actions-readonly"
                or len(paths) != 1
                or not paths[0].startswith(DAG_PREFIX)):
            raise ValueError("Unapproved DAG task identity/scope")
        tasks[item["task_id"]] = item
    return tasks


def _plan(name):
    if name not in DAG_PLANS:
        raise ValueError("Unregistered DAG plan")
    path, plan_id, _ = DAG_PLANS[name]
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    read_dag(raw, plan_id)
    return raw


def _check_round(round_no):
    if type(round_no) is not int or not 1 <= round_no <= pilot.MAX_ROUNDS:
        raise ValueError(f"Round must be an integer 1..{pilot.MAX_ROUNDS}")


def _require_native_run_evidence(evidence, native):
    """Fail closed on cross-run or cross-attempt evidence, even at the same SHA."""
    for item in evidence:
        if (not isinstance(item, dict)
                or type(item.get("workflow_run_id")) is not int
                or type(item.get("run_attempt")) is not int
                or item["workflow_run_id"] != native["run_id"]
                or item["run_attempt"] != native["attempt"]):
            raise ValueError("Evidence run provenance differs from native Actions context")


def round_matrix(raw, evidence, env, sha, round_no, capacity, deadline):
    """Return (matrix, schedule result) for one round. Proposal, never dispatch."""
    read_dag(raw)
    native = pilot.validate_context(env, sha)
    _require_native_run_evidence(evidence, native)
    _check_round(round_no)
    if type(deadline) is not int or deadline not in pilot.DEADLINES:
        raise ValueError("Unapproved per-agent deadline")
    result = schedule(raw, evidence, sha, capacity, max_capacity=CAPS[raw["plan_id"]])
    if any(e.get("round", 1) >= round_no for e in evidence):
        raise ValueError("Evidence from a round that has not finished")
    paths = {t["task_id"]: t["allowed_paths"][0]
             for w in validate_plan(raw)["waves"] for t in w["tasks"]}
    include = [{"slot": i + 1, "task_id": tid, "allowed_path": paths[tid],
                "deadline_seconds": deadline, "round": round_no}
               for i, tid in enumerate(result["dispatch"])]
    return {"include": include}, result


def prepare(raw, task_id, env, sha, temporary):
    return pilot.prepare(raw, task_id, env, sha, temporary,
                         tasks=read_dag(raw), plan_id=raw["plan_id"])


def finalize(raw, task_id, env, sha, temporary, outcome, artifact,
             agent_run=None, round_no=1):
    return pilot.finalize(raw, task_id, env, sha, temporary, outcome, artifact,
                          agent_run, tasks=read_dag(raw), round_no=round_no)


def _opt_int(value):
    return int(value) if value not in (None, "") else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("operation", choices=["matrix", "prepare", "finalize", "state"])
    ap.add_argument("--plan", required=True, choices=sorted(DAG_PLANS))
    ap.add_argument("--task-id")
    ap.add_argument("--round", default="1")
    ap.add_argument("--capacity", default=str(VALIDATED_CAPACITY))
    ap.add_argument("--deadline-seconds", default="600")
    ap.add_argument("--evidence-dir")
    ap.add_argument("--agent-outcome", default="config-only")
    ap.add_argument("--agent-exit-code")
    ap.add_argument("--agent-seconds")
    ap.add_argument("--out")
    ap.add_argument("--require-complete", action="store_true")
    args = ap.parse_args(argv)
    try:
        raw = _plan(args.plan)
        sha = pilot.git("rev-parse", "HEAD")
        env, temp = os.environ, os.environ.get("RUNNER_TEMP", "")
        round_no = _opt_int(args.round)
        if args.operation == "matrix":
            evidence = load_evidence(args.evidence_dir) if args.evidence_dir else []
            matrix, result = round_matrix(raw, evidence, env, sha, round_no,
                                          _opt_int(args.capacity),
                                          _opt_int(args.deadline_seconds))
            if not args.out:
                raise ValueError("Missing output file")
            with open(args.out, "a", encoding="utf-8") as fh:
                fh.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
                fh.write(f"count={len(matrix['include'])}\n")
                fh.write(f"outcome={result['outcome']}\n")
            print(f"round {round_no}: {result['outcome']}, dispatch {result['dispatch']}")
        elif args.operation == "prepare":
            prepare(raw, args.task_id, env, sha, temp)
            print("Prepared DAG task", args.task_id)
        elif args.operation == "finalize":
            if not args.out:
                raise ValueError("Missing artifact folder")
            run = {"exit_code": _opt_int(args.agent_exit_code),
                   "seconds": _opt_int(args.agent_seconds),
                   "deadline": _opt_int(args.deadline_seconds)}
            print(json.dumps(finalize(raw, args.task_id, env, sha, temp,
                                      args.agent_outcome, args.out, run, round_no)))
        else:
            native = pilot.validate_context(env, sha)
            evidence = load_evidence(args.evidence_dir)
            _require_native_run_evidence(evidence, native)
            result = schedule(raw, evidence, sha, 1)
            if not args.out:
                raise ValueError("Missing output file")
            with open(args.out, "x", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
                fh.write("\n")
            print(json.dumps({"outcome": result["outcome"],
                              "states": {t: v["state"] for t, v in result["tasks"].items()}}))
            if args.require_complete and result["outcome"] != "COMPLETE":
                raise ValueError("DAG did not complete: " + result["outcome"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(2, f"F2 DAG runner refused: {exc}\n")


if __name__ == "__main__":
    main()
