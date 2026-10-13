#!/usr/bin/env python3
"""F2.2 manual-only bounded OpenCode pilot. No GitHub writes or dispatch.

The operator launches a trusted MAIN-branch workflow_dispatch. This code
validates its static reviewed fixture, binds the immutable checkout SHA,
produces a small matrix, and gates each individual result by exact file owner.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from ocfilo_f2_plan import plan as validate_f2, REPO

REPO_ID = "1416228069"
MANIFEST = Path("examples/ocfilo-f2-execution-pilot.json")
MODEL = "opencode/big-pickle"
TASKS = {
    "f2-exec-alpha": "playground/f2/alpha.txt",
    "f2-exec-beta": "playground/f2/beta.txt",
    "f2-exec-gamma": "playground/f2/gamma.txt",
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_BYTES = 32768


def read_manifest(raw):
    if not isinstance(raw, dict):
        raise ValueError("Pilot manifest must be JSON object")
    summary = validate_f2(raw)
    if (raw["plan_id"] != "f2-exec"
            or raw["max_parallel"] != 3
            or len(raw["tasks"]) != 3
            or set(x["task_id"] for x in raw["tasks"]) != set(TASKS)
            or summary["wave_count"] != 1
            or summary["waves"][0]["worker_count"] != 3):
        raise ValueError("Only the reviewed independent 3-task pilot")
    for item in raw["tasks"]:
        if (item["depends_on"] != []
                or item.get("issue_number") != 10
                or item["model"] != MODEL
                or item["agent"] != "build"
                or item["profile"] != "base"
                or item["identity"] != "actions-readonly"
                or item["allowed_paths"] != [TASKS[item["task_id"]]]):
            raise ValueError("Unapproved F2 pilot task identity/scope")
    return {x["task_id"]: x for x in raw["tasks"]}


def validate_context(env, actual_sha):
    sha = env.get("GITHUB_SHA")
    if (env.get("GITHUB_REPOSITORY") != REPO
            or env.get("GITHUB_REPOSITORY_ID") != REPO_ID
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or not isinstance(sha, str) or not SHA.fullmatch(sha)
            or actual_sha != sha):
        raise ValueError("Pilot must be manual trusted main at exact checkout SHA")
    rid = env.get("GITHUB_RUN_ID")
    attempt = env.get("GITHUB_RUN_ATTEMPT")
    if (not rid or not rid.isdecimal() or int(rid) <= 0
            or not attempt or not attempt.isdecimal()
            or int(attempt) <= 0):
        raise ValueError("Missing native Actions run provenance")
    return {"head_sha": sha, "run_id": int(rid), "attempt": int(attempt)}


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True,
                       text=True, check=False)
    if r.returncode:
        raise ValueError("git " + args[0] + " failed")
    return r.stdout.strip()


def make_matrix(raw, count, env, actual_sha):
    tasks = read_manifest(raw)
    validate_context(env, actual_sha)
    if type(count) is not int or not 1 <= count <= 3:
        raise ValueError("Only 1..3 explicitly selected workers")
    selected = list(TASKS)[:count]
    return {"include": [
        {"slot": i + 1, "task_id": tid,
         "allowed_path": TASKS[tid]}
        for i, tid in enumerate(selected)
        if tid in tasks
    ]}


def prepare(raw, task_id, env, actual_sha, temporary):
    tasks = read_manifest(raw)
    evidence = validate_context(env, actual_sha)
    if task_id not in tasks:
        raise ValueError("Unknown F2 pilot task")
    task = tasks[task_id]
    temp = Path(temporary)
    temp.mkdir(parents=True, exist_ok=True)
    allow = TASKS[task_id]
    prompt = (
        task["prompt"] + "\n"
        "Sandbox: change ONLY the exact file " + allow +
        ". Create a short plain-text result, no credential access, "
        "no external service calls, no git push, no PR, no merge, "
        "no other file edits. Stop after this one file.\n"
    )
    (temp / "f2-prompt.txt").write_text(prompt, encoding="utf-8")
    config = {"$schema": "https://opencode.ai/config.json",
              "model": MODEL, "default_agent": "build"}
    data = json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n"
    Path("opencode.json").write_text(data, encoding="utf-8")
    (temp / "f2-config-sha256.txt").write_text(
        hashlib.sha256(Path("opencode.json").read_bytes()).hexdigest(), encoding="ascii")
    result = dict(
        schema_version=1, repository=REPO, repository_id=int(REPO_ID),
        plan_id="f2-exec", issue_number=10, task_id=task_id,
        model=MODEL, identity="actions-readonly",
        allowed_paths=[allow], checkout_sha=evidence["head_sha"],
        workflow_run_id=evidence["run_id"],
        run_attempt=evidence["attempt"],
        dispatch_allowed=False, publication_allowed=False,
    )
    (temp / "f2-context.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def evaluate_worktree(task_id, env, temporary, agent_outcome):
    temp = Path(temporary)
    result = json.loads((temp / "f2-context.json").read_text(encoding="utf-8"))
    expected = validate_context(env, git("rev-parse", "HEAD"))
    if (result["checkout_sha"] != expected["head_sha"]
            or result["workflow_run_id"] != expected["run_id"]
            or result["run_attempt"] != expected["attempt"]
            or result["allowed_paths"] != [TASKS[task_id]]
            or result["task_id"] != task_id):
        raise ValueError("Result provenance / file owner mismatch")
    config = Path("opencode.json")
    if (not config.is_file() or config.is_symlink()
            or hashlib.sha256(config.read_bytes()).hexdigest()
            != (temp / "f2-config-sha256.txt").read_text(encoding="ascii")):
        raise ValueError("Agent modified trusted tool configuration")
    # Read complete status including untracked changes; never silently ignore
    # an out-of-scope path or an unexpected rename/symlink.
    status = subprocess.run(
        ["git", "status", "--porcelain=1", "-z", "--untracked-files=all"],
        capture_output=True, check=True).stdout
    changed = []
    entries = status.split(b"\x00")
    for raw in entries:
        if not raw:
            continue
        if raw[:2] in (b"??", b" M", b"M ", b"MM", b" A", b"A "):
            p = raw[3:].decode("utf-8", "strict")
            if p != "opencode.json":
                changed.append(p)
        else:
            raise ValueError("Unexpected git status operation; deny changes")
    allowed = TASKS[task_id]
    if len(changed) != 1 or changed != [allowed]:
        raise ValueError("Agent wrote outside exact assigned file or no change")
    target = Path(allowed)
    if (not target.is_file() or target.is_symlink()
            or target.stat().st_size > MAX_BYTES):
        raise ValueError("Invalid output file type or size")
    if agent_outcome != "success":
        raise ValueError("OpenCode agent did not complete successfully")
    subprocess.run(["git", "add", "-N", "--", allowed], check=True)
    subprocess.run(["git", "diff", "--check", "HEAD", "--", allowed],
                   check=True, stdout=subprocess.DEVNULL)
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", allowed],
        check=True, capture_output=True).stdout
    if not patch or len(patch) > MAX_BYTES or b"GIT binary patch" in patch:
        raise ValueError("Missing/unsafe output patch")
    return patch, result


def finalize(raw, task_id, env, actual_sha, temporary, outcome, artifact):
    """Persist fail-closed evidence even when model execution fails."""
    tasks = read_manifest(raw)
    validate_context(env, actual_sha)
    if task_id not in tasks:
        raise ValueError("Unknown task")
    evidence = {"schema_version": 1, "repository": REPO,
                "repository_id": int(REPO_ID), "issue_number": 10,
                "model": MODEL, "identity": "actions-readonly",
                "allowed_paths": [TASKS[task_id]], "task_id": task_id,
                "checkout_sha": actual_sha,
                "workflow_run_id": int(env["GITHUB_RUN_ID"]),
                "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                "worker_outcome": "FAILED",
                "scope_verified": False, "published": False}
    dest = Path(artifact)
    dest.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        if outcome == "config-only":
            # No worker executed; in this mode absolutely no patch.
            evidence["worker_outcome"] = "CONFIG_ONLY"
        else:
            patch, _ = evaluate_worktree(task_id, env, temporary, outcome)
            (dest / "changes.patch").write_bytes(patch)
            evidence["worker_outcome"] = "SUCCEEDED"
            evidence["scope_verified"] = True
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        error = str(exc)[:240]
        evidence["error"] = error
    (dest / "result.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    if error:
        raise ValueError(error)
    return evidence


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("operation", choices=["matrix", "prepare", "finalize"])
    ap.add_argument("--worker-count", type=int, default=1)
    ap.add_argument("--task-id")
    ap.add_argument("--out")
    ap.add_argument("--agent-outcome", default="config-only")
    args = ap.parse_args()
    source = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    temp = os.environ.get("RUNNER_TEMP", "")
    try:
        sha = git("rev-parse", "HEAD")
        if args.operation == "matrix":
            matrix = make_matrix(source, args.worker_count, os.environ, sha)
            if not args.out:
                raise ValueError("Missing matrix output")
            with open(args.out, "a", encoding="utf-8") as out:
                out.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
            print(f"Validated exact main, selected {len(matrix['include'])} sandbox jobs")
        elif args.operation == "prepare":
            prepare(source, args.task_id, os.environ, sha, temp)
            print("Prepared F2 sandbox task", args.task_id)
        else:
            if not args.out:
                raise ValueError("Missing safe artifact folder")
            print(json.dumps(finalize(source, args.task_id, os.environ,
                                     sha, temp, args.agent_outcome, args.out)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(2, f"F2 bounded pilot refused: {exc}\n")


if __name__ == "__main__":
    main()
