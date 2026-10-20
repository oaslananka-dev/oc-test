#!/usr/bin/env python3
"""SHA-bound, opt-in F1 fixture PR repair through an unprivileged fleet worker.

Only pilot PR #2, one fixture path, one known CI check. Never merges.
Unknown dispatch/publish outcomes stay reserved and require reconciliation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

import ocfilo_pr_observer as observer
import ocfilo_repair_queue as queue
import oc_fleet_issue as controller
from oc_fleet_contract import scoped_patch
from oc_fleet_identity import github_user_identity, verify_commit

REPO = "oaslananka-dev/oc-test"
PR_NUMBER = 2
BRANCH = "ocfilo/f1-queue-02"
FILE = "playground/f1-smoke.txt"
WORKER = "MakerParsDev/oc-test"
EXPECTED_CHECK = "fixture-quality"
MODEL = "opencode/big-pickle"


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def approved_pr(target):
    pr = controller.gh(target, f"repos/{REPO}/pulls/{PR_NUMBER}")
    require(pr.get("number") == PR_NUMBER
            and pr.get("state") == "open" and pr.get("merged") is False
            and pr.get("draft") is False
            and (pr.get("head") or {}).get("ref") == BRANCH
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == REPO
            and (pr.get("base") or {}).get("ref") == "main"
            and ((pr.get("base") or {}).get("repo") or {}).get("full_name") == REPO,
            "Only the approved same-repository pilot PR is repairable")
    sha = (pr.get("head") or {}).get("sha")
    require(isinstance(sha, str) and controller.SHA.fullmatch(sha), "Bad PR SHA")
    return sha


def live_decision(target):
    sha = approved_pr(target)
    previous = os.environ.get("GH_TOKEN")
    try:
        os.environ["GH_TOKEN"] = target["GH_TOKEN"]
        decision = observer.collect(PR_NUMBER, sha)
    finally:
        if previous is None:
            os.environ.pop("GH_TOKEN", None)
        else:
            os.environ["GH_TOKEN"] = previous
    require(decision.get("head_sha") == sha, "Observer SHA mismatch")
    return sha, decision


def can_repair(decision):
    # The observer already checks trusted GitHub App IDs, stale SHA, and
    # ALL nonrepairable/pending CI statuses. Never obey PR comments.
    return (decision.get("state") == "REPAIR_REQUIRED"
            and decision.get("dispatch_allowed") is True
            and decision.get("pr_number") == PR_NUMBER
            and decision.get("repository") == REPO
            and decision.get("failing_checks") == [EXPECTED_CHECK]
            and decision.get("pending_checks") == []
            and decision.get("missing_checks") == [])


def build_manifest(sha, allowed):
    manifest = dict(
        schema_version=1, task_id="gh-2-" + uuid.uuid4().hex[:12],
        source="rules", repository=REPO, checkout_sha=sha,
        base_branch="main", issue_key="GH-2",
        prompt=("Repair the existing approved PR #2 on its exact checked-out head SHA. "
                "A trusted GitHub Actions fixture-quality check failed. "
                "Edit ONLY playground/f1-smoke.txt to contain exactly these three lines:\n"
                "ocfilo F1 worker proof\n"
                "Big Pickle ran on Ubuntu\n"
                "ocfilo F1 CI PASS\n"
                "End with a newline. Do not edit any other file, create any PR, "
                "change any check/workflow, or run publishing commands."),
        model=MODEL, profile="base", allowed_paths=[FILE],
        phase="implement", publish=False, integrations=[], tool_profile=""
    )
    from oc_fleet_contract import parse_manifest
    parse_manifest(json.dumps(manifest), allowed)
    return manifest


def publish_repair(target, sha, evidence, patch):
    """Apply the provenance-checked patch as a NEW fast-forward commit on same PR."""
    require(approved_pr(target) == sha, "PR head moved before repair publication")
    require(evidence.get("checkout_sha") == sha
            and evidence.get("repository") == REPO
            and evidence.get("paths") == [FILE]
            and hashlib.sha256(patch).hexdigest() == evidence.get("patch_sha256"),
            "Unexpected repair artifact provenance or path")
    with tempfile.TemporaryDirectory(prefix="oc-f1-repair-") as temp:
        checkout = Path(temp) / "target"
        controller.cmd(["gh", "repo", "clone", REPO, str(checkout), "--", "--quiet"], target)
        controller.cmd(["git", "-C", str(checkout), "checkout", "--detach", sha], target)
        controller.cmd(["git", "-C", str(checkout), "config", "core.autocrlf", "false"], target)
        controller.cmd(["git", "-C", str(checkout), "reset", "--hard", sha], target)
        require(controller.cmd(["git", "-C", str(checkout), "status", "--porcelain"], target)
                .stdout.strip() == "", "Dirty repair checkout")
        patchfile = Path(temp) / "repair.patch"
        patchfile.write_bytes(patch)
        controller.cmd(["git", "-C", str(checkout), "apply", "--check", str(patchfile)], target)
        controller.cmd(["git", "-C", str(checkout), "apply", str(patchfile)], target)
        paths, checked = scoped_patch(checkout, [FILE])
        require(paths == [FILE] and checked == patch, "Patch changed after validation")
        contents = (checkout / FILE).read_bytes()
        require(contents == (b"ocfilo F1 worker proof\n"
                             b"Big Pickle ran on Ubuntu\n"
                             b"ocfilo F1 CI PASS\n"), "Repair did not satisfy fixture criteria")
        require(approved_pr(target) == sha, "PR changed during validation")
        actor = controller.gh(target, "user")
        require(actor.get("login", "").lower() == "oaslananka", "Wrong publisher identity")
        name, email = github_user_identity(actor)
        for key, value in (("user.name", name), ("user.email", email)):
            controller.cmd(["git", "-C", str(checkout), "config", key, value], target)
        controller.cmd(["git", "-C", str(checkout), "add", "--", FILE], target)
        controller.cmd(["git", "-C", str(checkout), "commit", "-m",
                        "fix(test): restore approved F1 fixture acceptance"], target)
        verify_commit(checkout, name, email)
        commit = controller.cmd(["git", "-C", str(checkout), "rev-parse", "HEAD"], target).stdout.strip()
        require(controller.SHA.fullmatch(commit), "Invalid repair commit")
        # No force option: another writer always makes this fail safely.
        controller.cmd(["git", "-C", str(checkout), "-c", "credential.helper=",
                        "-c", "credential.helper=!gh auth git-credential",
                        "push", "origin", f"HEAD:refs/heads/{BRANCH}"], target)
        require(approved_pr(target) == commit, "Published PR SHA not confirmed")
        print("REPAIR_PUBLISHED", PR_NUMBER, commit, flush=True)
        return commit


def execute_once(db_path, dry_run=True):
    target = controller.identity("oaslananka")
    sha, decision = live_decision(target)
    print("F1_REPAIR_OBSERVED", PR_NUMBER, decision["state"], sha[:12], flush=True)
    if not can_repair(decision):
        print("NO_REPAIR", decision["state"], flush=True)
        return "no_work"
    worker = controller.identity("MakerParsDev")
    runner = controller.authorized_worker_repo(worker, WORKER)
    require(runner == WORKER, "Unexpected runner")
    # Read only the existing deployment allowlist; never expand it implicitly.
    secrets_env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "DOPPLER_TOKEN"):
        secrets_env.pop(key, None)
    listing = controller.cmd([
        "doppler", "secrets", "get", "FLEET_ALLOWED_REPOS", "--plain",
        "--project", "oc-test", "--config", "main",
    ], secrets_env, ok=False, deadline=25)
    require(listing.returncode == 0, "Existing Doppler allowlist unavailable")
    allowed = [v.strip() for v in listing.stdout.strip().split(",") if v.strip()]
    require(REPO.lower() in [v.lower() for v in allowed],
            "Pilot target is not in the pre-existing allowlist")
    manifest = build_manifest(sha, allowed)
    if dry_run:
        print("REPAIR_PLAN", PR_NUMBER, sha[:12], EXPECTED_CHECK, flush=True)
        return "planned"
    con = queue.connect(db_path)
    try:
        prior = con.execute(
            "SELECT COUNT(*) FROM repair_jobs WHERE repository=? AND pr_number=?",
            (REPO, PR_NUMBER)).fetchone()[0]
        delivery = f"f1-pr2-{sha[:14]}-{decision['repair_key']}-{prior + 1}"
        result = queue.reserve(con, decision, sha, delivery)
        print("REPAIR_RESERVATION", result["outcome"], flush=True)
        if result["outcome"] != "RESERVED":
            return result["outcome"]
        job = result["job"]
        # This is intentionally single-shot; unknown outcomes remain RESERVED.
        run_id = controller.dispatch(worker, manifest, runner=WORKER)
        queue.acknowledge(con, job["id"], run_id, sha)
        print("REPAIR_WORKER_RUN", run_id, flush=True)
        try:
            controller.await_run(worker, run_id, runner=WORKER)
            evidence, patch = controller.verify_artifact(worker, run_id, manifest, runner=WORKER)
        except ValueError:
            # Only release when run is independently proven terminal failure.
            # Uncertain artifact or network state remains DISPATCHED for inspection.
            live = controller.gh(worker, f"repos/{WORKER}/actions/runs/{run_id}")
            if (live.get("status") == "completed"
                    and live.get("conclusion") in ("failure", "cancelled", "timed_out")):
                queue.finalize(con, job["id"], run_id, sha, "FAILED")
            raise
        publish_repair(target, sha, evidence, patch)
        queue.finalize(con, job["id"], run_id, sha, "SUCCEEDED")
        return "published"
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path.home() / ".local/state/oc-fleet/f1-repairs.sqlite3")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    execute_once(args.db, dry_run=not args.run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError,
            json.JSONDecodeError) as error:
        print("REPAIR_BLOCKED", type(error).__name__, str(error)[:190], file=sys.stderr)
        sys.exit(1)
