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
import stat
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
DEADLINES = (300, 600, 900)
MAX_ROUNDS = 6
LOG_TAIL_BYTES = 65536
# Terminal outcomes recorded in evidence. PENDING/READY/RUNNING exist only as
# live GitHub job states; BLOCKED belongs to the future DAG runtime.
OUTCOMES = ("SUCCEEDED", "FAILED", "CANCELLED", "CONFIG_ONLY")
FAILURE_CLASSES = (
    "TIMEOUT", "RATE_LIMITED", "QUOTA_EXHAUSTED", "PROVIDER_ERROR", "NETWORK",
    "AGENT_ERROR", "NOT_STARTED", "CANCELLED", "NO_CHANGE", "SCOPE_VIOLATION",
    "CONFIG_TAMPERED", "OUTPUT_INVALID", "EVIDENCE_INVALID",
)
# Advisory only: a future retry runner must still bound attempts and never
# switch to a paid model. Class comes from an untrusted log; it never grants
# authority.
RETRYABLE = frozenset({"TIMEOUT", "RATE_LIMITED", "PROVIDER_ERROR", "NETWORK"})
LOG_PATTERNS = (  # order matters: quota wins over a bare 429
    ("QUOTA_EXHAUSTED", re.compile(
        r"quota|insufficient[ _]?(?:credits?|funds|balance)|billing|payment required"
        r"|(?:status|http|error)\W{0,4}402\b", re.I)),
    ("RATE_LIMITED", re.compile(
        r"rate[ _-]?limit|too many requests|(?:status|http|error)\W{0,4}429\b", re.I)),
    ("PROVIDER_ERROR", re.compile(
        r"service unavailable|bad gateway|gateway time-?out|internal server error"
        r"|overloaded|(?:status|http|error)\W{0,4}50[0234]\b", re.I)),
    ("NETWORK", re.compile(
        r"ECONNRESET|ECONNREFUSED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|socket hang up"
        r"|fetch failed", re.I)),
)


class WorkerError(ValueError):
    """Fail-closed worker rejection carrying an evidence failure class."""

    def __init__(self, message, failure_class="EVIDENCE_INVALID"):
        super().__init__(message)
        self.failure_class = failure_class


def idempotency_key(sha, task_id):
    """Stable across run attempts: one task on one checkout SHA."""
    return hashlib.sha256(f"{REPO_ID}:{sha}:{task_id}".encode()).hexdigest()


def classify_failure(outcome, exit_code=None, seconds=None, deadline=None, log_tail=""):
    if outcome == "cancelled":
        return "CANCELLED"
    if outcome == "skipped":
        return "NOT_STARTED"
    if exit_code == 124 or (exit_code == 137 and seconds is not None
                            and deadline is not None and seconds >= deadline):
        return "TIMEOUT"
    for name, pattern in LOG_PATTERNS:
        if pattern.search(log_tail):
            return name
    return "AGENT_ERROR"


def read_log_tail(temporary):
    try:
        with open(Path(temporary) / "f2-agent.log", "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _bounded_int(value, low, high):
    return type(value) is int and low <= value <= high


def validate_agent_run(run):
    """Strictly validate the workflow-provided execution record."""
    run = run or {}
    out = {k: run.get(k) for k in ("exit_code", "seconds", "deadline")}
    if ((out["exit_code"] is not None and not _bounded_int(out["exit_code"], 0, 255))
            or (out["seconds"] is not None and not _bounded_int(out["seconds"], 0, 86400))
            or (out["deadline"] is not None
                and (type(out["deadline"]) is not int or out["deadline"] not in DEADLINES))):
        raise ValueError("Invalid agent execution record")
    return out


def default_expected():
    return {t: {"allowed_paths": [p], "issue_number": 10, "model": MODEL,
                "identity": "actions-readonly"} for t, p in TASKS.items()}


def validate_evidence(ev, expected=None):
    """Consumer-side contract for result.json (schema 2). Raises ValueError.

    expected maps task_id to its plan entry (allowed_paths, issue_number,
    model, identity); default is the fixed F2.2 pilot."""
    expected = default_expected() if expected is None else expected
    try:
        outcome, cls = ev["worker_outcome"], ev["failure_class"]
        want = expected.get(ev["task_id"])
        ok = (ev["schema_version"] == 2 and ev["repository"] == REPO
              and ev["repository_id"] == int(REPO_ID) and want is not None
              and ev["issue_number"] == want["issue_number"]
              and ev["model"] == want["model"] and ev["identity"] == want["identity"]
              and ev["published"] is False
              and _bounded_int(ev.get("round", 1), 1, MAX_ROUNDS)
              and ev["allowed_paths"] == want["allowed_paths"]
              and isinstance(ev["checkout_sha"], str) and SHA.fullmatch(ev["checkout_sha"])
              and ev["idempotency_key"] == idempotency_key(ev["checkout_sha"], ev["task_id"])
              and outcome in OUTCOMES
              and ev["retryable"] is (cls in RETRYABLE)
              and (ev["scope_verified"] is True) == (outcome == "SUCCEEDED"))
        agent = (ev["agent_exit_code"], ev["agent_seconds"], ev["deadline_seconds"])
        validate_agent_run(dict(zip(("exit_code", "seconds", "deadline"), agent)))
        if outcome in ("SUCCEEDED", "CONFIG_ONLY"):
            ok = ok and cls is None
        elif outcome == "CANCELLED":
            ok = ok and cls == "CANCELLED"
        else:
            ok = ok and cls in FAILURE_CLASSES and cls != "CANCELLED"
        if outcome == "CONFIG_ONLY":
            ok = ok and agent == (None, None, None)
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed F2 evidence") from exc
    if not ok:
        raise ValueError("F2 evidence violates task state contract")
    return ev


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


def make_matrix(raw, count, env, actual_sha, deadline=600):
    tasks = read_manifest(raw)
    validate_context(env, actual_sha)
    if type(count) is not int or not 1 <= count <= 3:
        raise ValueError("Only 1..3 explicitly selected workers")
    if type(deadline) is not int or deadline not in DEADLINES:
        raise ValueError("Unapproved per-agent deadline")
    selected = list(TASKS)[:count]
    return {"include": [
        {"slot": i + 1, "task_id": tid,
         "allowed_path": TASKS[tid], "deadline_seconds": deadline}
        for i, tid in enumerate(selected)
        if tid in tasks
    ]}


def prepare(raw, task_id, env, actual_sha, temporary, tasks=None, plan_id="f2-exec"):
    """tasks: already-validated manifest items; default is the fixed F2.2 pilot."""
    tasks = read_manifest(raw) if tasks is None else tasks
    evidence = validate_context(env, actual_sha)
    if task_id not in tasks:
        raise ValueError("Unknown F2 pilot task")
    task = tasks[task_id]
    temp = Path(temporary)
    temp.mkdir(parents=True, exist_ok=True)
    allow = task["allowed_paths"][0]
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
        plan_id=plan_id, issue_number=task["issue_number"], task_id=task_id,
        model=MODEL, identity="actions-readonly",
        allowed_paths=[allow], checkout_sha=evidence["head_sha"],
        workflow_run_id=evidence["run_id"],
        run_attempt=evidence["attempt"],
        dispatch_allowed=False, publication_allowed=False,
    )
    (temp / "f2-context.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def evaluate_worktree(task_id, env, temporary, agent_outcome, agent_failure=None,
                      owners=None):
    owners = TASKS if owners is None else owners
    temp = Path(temporary)
    result = json.loads((temp / "f2-context.json").read_text(encoding="utf-8"))
    expected = validate_context(env, git("rev-parse", "HEAD"))
    if (result["checkout_sha"] != expected["head_sha"]
            or result["workflow_run_id"] != expected["run_id"]
            or result["run_attempt"] != expected["attempt"]
            or result["allowed_paths"] != [owners[task_id]]
            or result["task_id"] != task_id):
        raise WorkerError("Result provenance / file owner mismatch")
    config = Path("opencode.json")
    if (not config.is_file() or config.is_symlink()
            or hashlib.sha256(config.read_bytes()).hexdigest()
            != (temp / "f2-config-sha256.txt").read_text(encoding="ascii")):
        raise WorkerError("Agent modified trusted tool configuration", "CONFIG_TAMPERED")
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
            raise WorkerError("Unexpected git status operation; deny changes",
                              "SCOPE_VIOLATION")
    allowed = owners[task_id]
    if any(p != allowed for p in changed):
        raise WorkerError("Agent wrote outside exact assigned file", "SCOPE_VIOLATION")
    if agent_outcome != "success":
        raise WorkerError("OpenCode agent did not complete successfully",
                          agent_failure or classify_failure(agent_outcome))
    if not changed:
        raise WorkerError("Agent produced no change to the exact assigned file", "NO_CHANGE")
    target = Path(allowed)
    if (not target.is_file() or target.is_symlink()
            or target.stat().st_size > MAX_BYTES):
        raise WorkerError("Invalid output file type or size", "OUTPUT_INVALID")
    # A worker may give a new *.py a shebang and chmod +x. The trusted
    # integrator allows only ordinary 100644 source patches. Normalize
    # *new* exact-owner files before hashing the Git diff. Never mutate
    # an existing tracked executable or expand the worker's write scope.
    if stat.S_IMODE(target.stat().st_mode) & 0o111:
        indexed = subprocess.run(["git", "ls-files", "--error-unmatch",
                                  "--", allowed], capture_output=True)
        if indexed.returncode == 0:
            raise WorkerError("Tracked executable worker output denied",
                              "OUTPUT_INVALID")
        target.chmod(stat.S_IMODE(target.stat().st_mode) & ~0o111)
    if stat.S_IMODE(target.stat().st_mode) & 0o111:
        raise WorkerError("Executable output could not be normalized",
                          "OUTPUT_INVALID")
    subprocess.run(["git", "add", "-N", "--", allowed], check=True)
    subprocess.run(["git", "diff", "--check", "HEAD", "--", allowed],
                   check=True, stdout=subprocess.DEVNULL)
    patch = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--", allowed],
        check=True, capture_output=True).stdout
    if not patch or len(patch) > MAX_BYTES or b"GIT binary patch" in patch:
        raise WorkerError("Missing/unsafe output patch", "OUTPUT_INVALID")
    return patch, result


def finalize(raw, task_id, env, actual_sha, temporary, outcome, artifact,
             agent_run=None, tasks=None, round_no=1):
    """Persist fail-closed evidence even when model execution fails."""
    tasks = read_manifest(raw) if tasks is None else tasks
    validate_context(env, actual_sha)
    if task_id not in tasks:
        raise ValueError("Unknown task")
    if type(round_no) is not int or not 1 <= round_no <= MAX_ROUNDS:
        raise ValueError("Invalid dispatch round")
    owners = {t: x["allowed_paths"][0] for t, x in tasks.items()}
    evidence = {"schema_version": 2, "repository": REPO,
                "repository_id": int(REPO_ID),
                "issue_number": tasks[task_id]["issue_number"],
                "model": MODEL, "identity": "actions-readonly",
                "allowed_paths": [owners[task_id]], "task_id": task_id,
                "round": round_no,
                "checkout_sha": actual_sha,
                "idempotency_key": idempotency_key(actual_sha, task_id),
                "workflow_run_id": int(env["GITHUB_RUN_ID"]),
                "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                "worker_outcome": "FAILED", "failure_class": None,
                "retryable": False, "agent_exit_code": None,
                "agent_seconds": None, "deadline_seconds": None,
                "scope_verified": False, "published": False}
    dest = Path(artifact)
    dest.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        if outcome == "config-only":
            # No worker executed; in this mode absolutely no patch.
            evidence["worker_outcome"] = "CONFIG_ONLY"
        else:
            run = validate_agent_run(agent_run)
            evidence["agent_exit_code"] = run["exit_code"]
            evidence["agent_seconds"] = run["seconds"]
            evidence["deadline_seconds"] = run["deadline"]
            failure = None
            if outcome != "success":
                failure = classify_failure(outcome, run["exit_code"], run["seconds"],
                                           run["deadline"], read_log_tail(temporary))
            patch, _ = evaluate_worktree(task_id, env, temporary, outcome, failure,
                                         owners)
            (dest / "changes.patch").write_bytes(patch)
            evidence["worker_outcome"] = "SUCCEEDED"
            evidence["scope_verified"] = True
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        error = str(exc)[:240]
        cls = getattr(exc, "failure_class", "EVIDENCE_INVALID")
        evidence["error"] = error
        evidence["failure_class"] = cls
        evidence["retryable"] = cls in RETRYABLE
        if cls == "CANCELLED":
            evidence["worker_outcome"] = "CANCELLED"
    validate_evidence(evidence, {t: {k: x[k] for k in (
        "allowed_paths", "issue_number", "model", "identity")} for t, x in tasks.items()})
    (dest / "result.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    if error:
        raise WorkerError(error, evidence["failure_class"])
    return evidence


def opt_int(value):
    """Workflow outputs are empty strings when the agent step never ran."""
    return int(value) if value not in (None, "") else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("operation", choices=["matrix", "prepare", "finalize"])
    ap.add_argument("--worker-count", type=int, default=1)
    ap.add_argument("--task-id")
    ap.add_argument("--out")
    ap.add_argument("--agent-outcome", default="config-only")
    ap.add_argument("--agent-exit-code")
    ap.add_argument("--agent-seconds")
    ap.add_argument("--deadline-seconds")
    args = ap.parse_args()
    source = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    temp = os.environ.get("RUNNER_TEMP", "")
    try:
        sha = git("rev-parse", "HEAD")
        if args.operation == "matrix":
            deadline = opt_int(args.deadline_seconds)
            matrix = make_matrix(source, args.worker_count, os.environ, sha,
                                 600 if deadline is None else deadline)
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
            run = {"exit_code": opt_int(args.agent_exit_code),
                   "seconds": opt_int(args.agent_seconds),
                   "deadline": opt_int(args.deadline_seconds)}
            print(json.dumps(finalize(source, args.task_id, os.environ, sha, temp,
                                      args.agent_outcome, args.out, run)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(2, f"F2 bounded pilot refused: {exc}\n")


if __name__ == "__main__":
    main()
