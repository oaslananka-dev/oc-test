#!/usr/bin/env python3
"""ocfilo: validated parallel tasks with exact, disjoint file ownership.

Produce on-disk dispatch manifests. NO GitHub API calls and NO subprocess.
The trusted controller explicitly invokes the already reviewed dispatcher.
"""
import argparse
import json
import re
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
ID = re.compile(r"[a-z0-9][a-z0-9-]{1,48}\Z")
SAFE_PATH = re.compile(r"playground/[A-Za-z0-9_.\-/]+\Z")
FREE = {"opencode/big-pickle","opencode/ling-3.1-flash-free","opencode/nemotron-3-ultra-free"}
PROFILES = {"base","runner-check","mcp-fixture","plugin-proof"}
REVIEW = {"none","codacy-local"}
MAX_WORKERS = 3


def validate(batch):
    if not isinstance(batch, dict) or set(batch) != {"batch_id", "workers"}:
        raise ValueError("Batch requires only batch_id and workers")
    if not ID.fullmatch(str(batch["batch_id"])):
        raise ValueError("Invalid batch id")
    workers = batch["workers"]
    if not isinstance(workers, list) or not 1 <= len(workers) <= MAX_WORKERS:
        raise ValueError("Parallel worker budget must be 1..3")
    result = []
    seen_ids = set()
    paths_in_use = {}
    for i, task in enumerate(workers):
        if not isinstance(task, dict):
            raise ValueError(f"Worker {i} must be an object")
        supported = {"task_id", "repository", "phase", "prompt", "model", "agent",
                     "profile", "review_profile", "publish", "fix_rounds", "allowed_paths"}
        if set(task) - supported:
            raise ValueError("Unapproved worker manifest fields")
        tid = task.get("task_id")
        if not isinstance(tid, str) or not ID.fullmatch(tid) or tid in seen_ids:
            raise ValueError("Invalid or repeated task id")
        seen_ids.add(tid)
        if not tid.startswith(batch["batch_id"]+"-"):
            raise ValueError("Task ID must be namespaced to batch id")
        if task.get("repository") != REPO or task.get("phase") != "implement":
            raise ValueError("Batch only allows isolated implementation in oc-test")
        if task.get("model") not in FREE or task.get("agent") != "build":
            raise ValueError("Unapproved model/agent")
        if task.get("profile") not in PROFILES or task.get("review_profile","none") not in REVIEW:
            raise ValueError("Unapproved tool profile")
        if task.get("publish") is not False:
            raise ValueError("Parallel worker cannot push or publish")
        if type(task.get("fix_rounds",1)) is not int or task.get("fix_rounds",1) != 1:
            raise ValueError("F1 parallel worker repair budget must be one")
        if not isinstance(task.get("prompt"), str) or not 4 <= len(task["prompt"]) <= 3000:
            raise ValueError("Invalid task prompt")
        owns=task.get("allowed_paths")
        if not isinstance(owns,list) or not 1 <= len(owns) <= 8:
            raise ValueError("Every parallel worker must own 1..8 exact paths")
        if len(set(owns))!=len(owns):
            raise ValueError("Duplicated paths in one task")
        for file in owns:
            if (not isinstance(file,str) or not SAFE_PATH.fullmatch(file)
                or any(piece in ("",".","..") for piece in file.split("/"))
                or "/__pycache__/" in "/"+file+"/" or file.endswith(".pyc")):
                raise ValueError("Unapproved file path")
            for earlier in paths_in_use:
                if file == earlier or file.startswith(earlier+"/") or earlier.startswith(file+"/"):
                    raise ValueError(f"Overlapping writable paths: {file}, {earlier}")
            paths_in_use[file]=tid
        profile=task.get("review_profile","none")
        if profile=="codacy-local" and not all(path.endswith(".py") for path in owns):
            raise ValueError("Codacy F1 profile supports only Python changes")
        result.append({**task, "review_profile":profile, "fix_rounds":1})
    return {"batch_id":batch["batch_id"],"worker_count":len(result),
            "workers":result,"path_owners":paths_in_use}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--plan",required=True)
    p.add_argument("--output-dir",required=True)
    args=p.parse_args()
    source=json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    b=validate(source)
    folder=Path(args.output_dir)
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit("Refusing to overwrite existing generated task files")
    folder.mkdir(parents=True,exist_ok=True)
    for w in b["workers"]:
        (folder/(w["task_id"]+".json")).write_text(json.dumps(w,indent=2)+"\n",encoding="utf-8")
    (folder/"batch-index.json").write_text(
        json.dumps({"batch_id":b["batch_id"],"worker_count":b["worker_count"],
                    "path_owners":b["path_owners"],
                    "task_files":[w["task_id"]+".json" for w in b["workers"]]},indent=2)+"\n",
        encoding="utf-8")
    print(json.dumps({"batch_id":b["batch_id"],"worker_count":b["worker_count"],
                      "task_files":[w["task_id"]+".json" for w in b["workers"]],
                      "path_owners":b["path_owners"]},indent=2))

if __name__=="__main__":
    main()
