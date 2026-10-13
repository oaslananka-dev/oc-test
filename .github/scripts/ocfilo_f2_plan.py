#!/usr/bin/env python3
"""F2 offline orchestration planner: deterministic, read-only, no worker dispatch.

Reuse the F1 task/model/path restrictions. This module neither executes agents
nor accesses GitHub, Multica, the network, subprocesses or any credentials.
A summary is NOT a dispatch manifest and never contains task prompts.
"""
import argparse
import json
from pathlib import Path

from ocfilo_batch import FREE, ID, SAFE_PATH

REPO = "oaslananka-dev/oc-test"
MAX_TASKS = 10
MAX_PARALLEL = 10
PROFILES = frozenset({"base", "runner-check"})
IDENTITIES = frozenset({"actions-readonly"})
ROOT_FIELDS = frozenset({
    "schema_version", "plan_id", "repository", "run_mode",
    "max_parallel", "tasks",
})
TASK_FIELDS = frozenset({
    "task_id", "prompt", "model", "agent", "profile", "identity",
    "allowed_paths", "depends_on", "issue_number",
})
REQUIRED_TASK_FIELDS = TASK_FIELDS - {"depends_on", "issue_number"}
BAD_COMPONENTS = frozenset({
    ".git", ".github", ".env", "__pycache__", "node_modules",
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


def _exact_positive(value, upper, label):
    if type(value) is not int or not 1 <= value <= upper:
        raise ValueError(f"{label} must be an integer 1..{upper}")


def _path(path):
    if not isinstance(path, str) or not SAFE_PATH.fullmatch(path):
        raise ValueError("Only exact F1 sandbox paths under playground/ allowed")
    components = path.split("/")
    if (len(components) < 2
            or any(not part or part in (".", "..") or
                   part.casefold() in BAD_COMPONENTS
                   for part in components)
            or components[-1].startswith(".")
            or "." not in components[-1]
            or path.endswith(".pyc")):
        raise ValueError("Unsafe or non-file writable scope")
    return path


def plan(data):
    if not isinstance(data, dict) or set(data) != ROOT_FIELDS:
        raise ValueError("Unknown/missing plan fields; fail closed")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    if data["repository"] != REPO or data["run_mode"] != "plan-only":
        raise ValueError("F2 only permits a plan-only oc-test preview")
    name = data["plan_id"]
    if not isinstance(name, str) or not ID.fullmatch(name):
        raise ValueError("Invalid plan identifier")
    _exact_positive(data["max_parallel"], MAX_PARALLEL, "max_parallel")
    tasks = data["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        raise ValueError("Task budget must be 1..10")

    validated = {}
    owners = {}
    for index, item in enumerate(tasks):
        if (not isinstance(item, dict)
                or not REQUIRED_TASK_FIELDS.issubset(item)
                or set(item) - TASK_FIELDS):
            raise ValueError(f"Task {index}: unknown/missing fields")
        tid = item["task_id"]
        if (not isinstance(tid, str) or not ID.fullmatch(tid)
                or not tid.startswith(name + "-") or tid in validated):
            raise ValueError("Invalid, duplicate or unscoped task identifier")
        if (item["model"] not in FREE or item["agent"] != "build"
                or item["profile"] not in PROFILES
                or item["identity"] not in IDENTITIES):
            raise ValueError("Unapproved model, agent, tool or identity profile")
        prompt = item["prompt"]
        if (not isinstance(prompt, str) or not 4 <= len(prompt) <= 3000
                or "\0" in prompt or not prompt.strip()):
            raise ValueError("Missing/unsafe task prompt")
        issue = item.get("issue_number")
        if issue is not None and (type(issue) is not int or issue < 1):
            raise ValueError("Invalid GitHub issue reference")
        dependencies = item.get("depends_on", [])
        if (not isinstance(dependencies, list)
                or any(not isinstance(x, str) for x in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ValueError("Invalid task dependencies")
        if tid in dependencies:
            raise ValueError("Task cannot depend on itself")
        paths = item["allowed_paths"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 8:
            raise ValueError("1..8 exact writable files required per task")
        if len({p.casefold() for p in paths if isinstance(p, str)}) != len(paths):
            raise ValueError("Duplicate writable file in task")
        for p in paths:
            _path(p)
            folded = p.casefold()
            for previous in owners:
                if (folded == previous or folded.startswith(previous + "/")
                        or previous.startswith(folded + "/")):
                    raise ValueError(f"Overlapping file ownership: {p}")
            owners[folded] = (p, tid)
        validated[tid] = {
            "task_id": tid,
            "depends_on": list(dependencies),
            "model": item["model"],
            "profile": item["profile"],
            "identity": item["identity"],
            "issue_number": issue,
            "allowed_paths": paths[:],
        }
    for tid, entry in validated.items():
        if set(entry["depends_on"]) - validated.keys():
            raise ValueError(f"{tid}: unknown dependency")

    finished = set()
    remaining = list(validated)
    waves = []
    while remaining:
        ready = [tid for tid in remaining
                 if set(validated[tid]["depends_on"]) <= finished]
        if not ready:
            raise ValueError("Circular task dependency")
        batch = ready[:data["max_parallel"]]
        waves.append({
            "wave": len(waves) + 1,
            "worker_count": len(batch),
            "tasks": [validated[tid] for tid in batch],
        })
        finished.update(batch)
        remaining = [tid for tid in remaining if tid not in finished]

    return {
        "schema_version": 1, "plan_id": name, "repository": REPO,
        "run_mode": "plan-only", "max_parallel": data["max_parallel"],
        "task_count": len(validated), "wave_count": len(waves),
        "waves": waves,
        "path_owners": {original: owner for original, owner in
                        sorted(owners.values())},
        "execution_authorized": False,
        "dispatch_allowed": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source = Path(args.input)
        if source.stat().st_size > 64000:
            raise ValueError("Plan exceeds 64 KB")
        result = plan(json.loads(source.read_text(encoding="utf-8-sig")))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"F2 plan rejected: {exc}\n")
    print(json.dumps({
        "plan_id": result["plan_id"],
        "task_count": result["task_count"],
        "wave_count": result["wave_count"],
        "dispatch_allowed": False,
        "output": str(output),
    }))


if __name__ == "__main__":
    main()


