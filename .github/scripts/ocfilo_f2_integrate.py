#!/usr/bin/env python3
"""F2.4 verified integration assembler. Local only: never pushes or opens a PR.

Turns the artifacts of ONE complete, successful DAG run into a single local
branch for human review. Everything is checked before any git mutation:
GitHub run metadata, artifact identity, schema-2 evidence, scheduler state
(every task SUCCEEDED), strict single-file patch shape, exact base SHA, clean
worktree and that the result changes exactly the owned paths. Merging stays a
human decision.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import ocfilo_f2_dag as dag
import ocfilo_f2_pilot as pilot
from ocfilo_f2_plan import plan as validate_plan
from ocfilo_f2_sched import schedule

WORKFLOW_PATH = ".github/workflows/ocfilo-f2-dag-run.yml"
ARTIFACT = re.compile(r"ocfilo-f2-(\d+)-(\d+)-r(\d+)-([a-z0-9][a-z0-9-]{1,48})\Z")
BRANCH = re.compile(r"f2-integration/[a-z0-9][a-z0-9._-]{0,60}\Z")
TASK_FILES = {"result.json", "changes.patch"}
MAX_EVIDENCE_BYTES = 16384
IDENTITY = ("ocfilo-integrator", "ocfilo-integrator@users.noreply.github.com")
HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def verify_run(meta, run_id, sha):
    """GitHub Actions run metadata (as returned by the runs API) must match."""
    if (not isinstance(meta, dict) or meta.get("id") != run_id
            or meta.get("head_sha") != sha
            or meta.get("head_branch") != "main"
            or meta.get("event") != "workflow_dispatch"
            or meta.get("path") != WORKFLOW_PATH
            or meta.get("status") != "completed"
            or meta.get("conclusion") != "success"
            or type(meta.get("run_attempt")) is not int
            or (meta.get("repository") or {}).get("full_name") != pilot.REPO
            or (meta.get("repository") or {}).get("id") != int(pilot.REPO_ID)):
        raise ValueError("Run metadata is not a successful trusted manual DAG run on this SHA")
    return meta["run_attempt"]


def check_patch(patch, path):
    """Strict shape: one regular file, nothing but add/modify of exactly `path`."""
    if not patch or len(patch) > pilot.MAX_BYTES:
        raise ValueError(f"{path}: empty or oversized patch")
    try:
        lines = patch.decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: patch is not UTF-8 text") from exc
    if lines[-1] != "":
        raise ValueError(f"{path}: patch must end with a newline")
    lines.pop()
    try:
        _check_lines(lines, path)
    except IndexError as exc:
        raise ValueError(f"{path}: truncated patch") from exc


def _check_lines(lines, path):
    if lines[0] != f"diff --git a/{path} b/{path}":
        raise ValueError(f"{path}: patch header is not exactly the owned file")
    i, new_file = 1, False
    if lines[i] == "new file mode 100644":
        new_file, i = True, i + 1
    if not re.fullmatch(r"index [0-9a-f]{7,40}\.\.[0-9a-f]{7,40}(?: 100644)?", lines[i]):
        raise ValueError(f"{path}: unexpected patch metadata")
    i += 1
    if lines[i:i + 2] != [("--- /dev/null" if new_file else f"--- a/{path}"), f"+++ b/{path}"]:
        raise ValueError(f"{path}: unexpected file markers")
    hunks = 0
    for line in lines[i + 2:]:
        if HUNK.match(line):
            hunks += 1
        elif not line or line[0] not in " +-\\":
            raise ValueError(f"{path}: unexpected patch line")
    if not hunks or any(x.startswith("diff --git") for x in lines[1:]):
        raise ValueError(f"{path}: missing hunk or extra file in patch")


def _load(art_dir, tasks, expected, sha, run_id, attempt):
    root = Path(art_dir)
    found = {}
    for d in sorted(root.iterdir()):
        if d.name.startswith("ocfilo-f2-dagstate-"):
            continue
        m = ARTIFACT.fullmatch(d.name)
        if not m or d.is_symlink() or not d.is_dir():
            raise ValueError(f"Unexpected artifact: {d.name}")
        run, att, rnd, tid = int(m[1]), int(m[2]), int(m[3]), m[4]
        files = {f.name: f for f in d.iterdir()}
        if not set(files) <= TASK_FILES or "result.json" not in files or any(
                f.is_symlink() or not f.is_file() for f in files.values()):
            raise ValueError(f"{d.name}: unexpected artifact contents")
        if files["result.json"].stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError(f"{d.name}: oversized evidence")
        ev = json.loads(files["result.json"].read_text(encoding="utf-8"))
        pilot.validate_evidence(ev, expected)
        if ((ev["workflow_run_id"], ev["run_attempt"], ev["round"], ev["task_id"])
                != (run, att, rnd, tid) or run != run_id or att != attempt
                or ev["checkout_sha"] != sha or tid not in tasks):
            raise ValueError(f"{d.name}: artifact name and evidence disagree")
        has_patch = "changes.patch" in files
        if has_patch != (ev["worker_outcome"] == "SUCCEEDED"):
            raise ValueError(f"{d.name}: patch presence does not match outcome")
        found.setdefault(tid, []).append((ev, files.get("changes.patch")))
    return found


def verify(raw_plan, art_dir, run_meta, run_id, sha):
    """Return (ordered [(task_id, path, patch bytes, evidence)], all evidence)."""
    tasks = dag.read_dag(raw_plan)
    attempt = verify_run(run_meta, run_id, sha)
    expected = {t: {k: x[k] for k in ("allowed_paths", "issue_number", "model", "identity")}
                for t, x in tasks.items()}
    found = _load(art_dir, tasks, expected, sha, run_id, attempt)
    evidence = [ev for items in found.values() for ev, _ in items]
    state = schedule(raw_plan, evidence, sha, 1)
    if state["outcome"] != "COMPLETE":
        raise ValueError("DAG run is not complete: " + state["outcome"])
    ordered = []
    for wave in validate_plan(raw_plan)["waves"]:
        for t in wave["tasks"]:
            tid = t["task_id"]
            wins = [(ev, f) for ev, f in found[tid] if ev["worker_outcome"] == "SUCCEEDED"]
            if len(wins) != 1:
                raise ValueError(f"{tid}: expected exactly one SUCCEEDED evidence")
            path = tasks[tid]["allowed_paths"][0]
            patch = wins[0][1].read_bytes()
            check_patch(patch, path)
            ordered.append((tid, path, patch, wins[0][0]))
    if len({p for _, p, _, _ in ordered}) != len(ordered):
        raise ValueError("Two tasks own the same path")
    return ordered, evidence


def _git(repo, *args, data=None, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], input=data,
                       capture_output=True, check=False)
    if check and r.returncode:
        raise ValueError(f"git {args[0]} failed: {r.stderr.decode('utf-8', 'replace')[:160]}")
    return r.stdout


def integrate(raw_plan, art_dir, run_meta, run_id, sha, repo, branch=None):
    """Verify everything; optionally create ONE local branch. Returns the manifest."""
    ordered, _ = verify(raw_plan, art_dir, run_meta, run_id, sha)
    if _git(repo, "rev-parse", "HEAD").decode().strip() != sha:
        raise ValueError("Repository HEAD is not the evidence checkout SHA")
    if _git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Worktree must be clean")
    if branch is not None and not BRANCH.fullmatch(branch):
        raise ValueError("Branch must look like f2-integration/<name>")
    for tid, path, patch, _ in ordered:
        _git(repo, "apply", "--check", "--index", "-", data=patch)
    commit = None
    if branch is not None:
        if _git(repo, "branch", "--list", branch).strip():
            raise ValueError("Branch already exists")
        _git(repo, "checkout", "-q", "-b", branch)
        try:
            for _, _, patch, _ in ordered:
                _git(repo, "apply", "--index", "-", data=patch)
            changed = sorted(_git(repo, "diff", "--cached", "--name-only", "-z"
                                  ).decode().split("\x00")[:-1])
            if changed != sorted(p for _, p, _, _ in ordered):
                raise ValueError("Integrated change set differs from the owned paths")
            msg = (f"feat(f2): integrate {raw_plan['plan_id']} run {run_id}\n\n"
                   + "\n".join(f"- {tid}: {path}" for tid, path, _, _ in ordered)
                   + f"\n\nBase {sha}. Verified by ocfilo_f2_integrate.py; review required.\n")
            _git(repo, "-c", f"user.name={IDENTITY[0]}", "-c", f"user.email={IDENTITY[1]}",
                 "commit", "-q", "--no-gpg-sign", "-m", msg)
            commit = _git(repo, "rev-parse", "HEAD").decode().strip()
        except (ValueError, OSError):
            _git(repo, "reset", "-q", "--hard", sha, check=False)
            _git(repo, "checkout", "-q", "--detach", sha, check=False)
            _git(repo, "branch", "-D", branch, check=False)
            raise
    return {
        "schema_version": 1, "repository": pilot.REPO, "plan_id": raw_plan["plan_id"],
        "base_sha": sha, "run_id": run_id, "run_attempt": run_meta["run_attempt"],
        "branch": branch, "commit": commit,
        "tasks": [{"task_id": tid, "path": path, "round": ev["round"],
                   "patch_sha256": hashlib.sha256(patch).hexdigest(),
                   "evidence_idempotency_key": ev["idempotency_key"],
                   "agent_seconds": ev["agent_seconds"]}
                  for tid, path, patch, ev in ordered],
        "pushed": False, "review_required": True, "merge_allowed": False,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, choices=sorted(dag.DAG_PLANS))
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--run-json", required=True, help="GitHub runs API JSON for the run")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--branch", help="create this local branch; omit for a dry run")
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args(argv)
    try:
        raw = dag._plan(args.plan)
        meta = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
        if not pilot.SHA.fullmatch(args.sha):
            raise ValueError("Exact 40-hex SHA required")
        manifest = integrate(raw, args.artifacts, meta, meta.get("id"), args.sha,
                             args.repo, args.branch)
        with Path(args.manifest).open("x", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(2, f"F2 integration refused: {exc}\n")
    print(json.dumps({"branch": manifest["branch"], "commit": manifest["commit"],
                      "tasks": len(manifest["tasks"]), "pushed": False}))


if __name__ == "__main__":
    main()
