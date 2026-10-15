#!/usr/bin/env python3
"""Read-only worker artifact -> separately authorized PR publication (pilot same repo).

Invoked only from trusted main workflow_dispatch with its own GitHub token.
Never runs untrusted code, never changes main, never merges.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from oc_fleet_contract import MAX_PATCH_BYTES, ISSUE_RE, ID_RE, SHA_RE, scoped_patch
from oc_fleet_identity import github_user_identity, verify_commit

REPO = "oaslananka-dev/oc-test"
REPO_ID = 1416228069
WORKFLOW = "oc-fleet-worker.yml"
TRUSTED_SCOPES = ["playground/fleet/"]

def refuse(reason):
    raise ValueError(reason)

def command(*args, cwd=None, json_result=False):
    data = subprocess.check_output(args, cwd=str(cwd) if cwd else None, text=True)
    return json.loads(data) if json_result else data.strip()

def gh(path):
    return command("gh", "api", path, json_result=True)

def verify_source_run(run, workflow_id, run_id, publisher_sha):
    if (type(run_id) is not int or run_id < 1
            or type(workflow_id) is not int or workflow_id < 1):
        refuse("invalid run/workflow identity")
    if (run.get("id") != run_id or run.get("workflow_id") != workflow_id
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("head_sha") != publisher_sha
            or type(run.get("run_attempt")) is not int
            or run["run_attempt"] < 1
            or (run.get("repository") or {}).get("id") != REPO_ID
            or (run.get("repository") or {}).get("full_name") != REPO):
        refuse("source workflow execution is not authorized")
    return run["run_attempt"]

def verify_jobs(jobs):
    if not isinstance(jobs, list) or len(jobs) != 2:
        refuse("unexpected source job count")
    state = {j.get("name"): j.get("conclusion") for j in jobs}
    if state != {"validate": "success", "execute": "success"}:
        refuse("source did not execute trusted validation and worker jobs")

def verify_artifact_metadata(items, run_id, attempt, task_id):
    if not isinstance(items, list):
        refuse("artifact list missing")
    name = f"fleet-{run_id}-{attempt}-{task_id}"
    matching = [a for a in items if a.get("name") == name]
    if len(matching) != 1 or matching[0].get("expired") is not False:
        refuse("unique unexpired artifact not found")
    if (matching[0].get("workflow_run") or {}).get("id") != run_id:
        refuse("artifact provenance does not match source workflow")
    return name

def verify_evidence(ev, patch, run_id, attempt, sha, task_id):
    if type(ev) is not dict or set(ev) != {
        "schema_version", "task_id", "source", "repository", "checkout_sha",
        "issue_key", "run_id", "run_attempt", "outcome",
        "paths", "patch_sha256", "published"
    }:
        refuse("malformed result.json")
    if (ev["schema_version"] != 1 or ev["task_id"] != task_id
            or not ID_RE.fullmatch(task_id)
            or ev["repository"] != REPO or ev["checkout_sha"] != sha
            or ev["run_id"] != str(run_id)
            or ev["run_attempt"] != str(attempt)
            or ev["source"] not in ("frontier", "rules")
            or not isinstance(ev["issue_key"], str)
            or not ISSUE_RE.fullmatch(ev["issue_key"])
            or ev["outcome"] != "SUCCEEDED" or ev["published"] is not False):
        refuse("source identity or outcome mismatch")
    if (not isinstance(patch, bytes) or not patch
            or len(patch) > MAX_PATCH_BYTES or b"GIT binary patch" in patch
            or b"Binary files " in patch):
        refuse("invalid bounded patch")
    if (not isinstance(ev["patch_sha256"], str)
            or ev["patch_sha256"] != hashlib.sha256(patch).hexdigest()):
        refuse("patch digest mismatch")
    paths = ev["paths"]
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 40
            or len(set(paths)) != len(paths)
            or not all(isinstance(p, str)
                       and p.startswith("playground/fleet/")
                       and ".." not in p and "/." not in p for p in paths)):
        refuse("artifact requested forbidden paths")
    return paths

def verify_local_patch(checkout, patch_path, expected_paths, expected_sha, expected_digest):
    if command("git", "-C", str(checkout), "rev-parse", "HEAD") != expected_sha:
        refuse("trusted checkout SHA differs from worker source")
    if command("git", "-C", str(checkout), "status", "--porcelain"):
        refuse("trusted checkout must be clean")
    subprocess.run(["git", "-C", str(checkout), "apply", "--check",
                    str(patch_path)], check=True)
    subprocess.run(["git", "-C", str(checkout), "apply",
                    str(patch_path)], check=True)
    actual_paths, normalized = scoped_patch(checkout, TRUSTED_SCOPES)
    if sorted(actual_paths) != sorted(expected_paths):
        refuse("file set changed during patch application")
    if hashlib.sha256(normalized).hexdigest() != expected_digest:
        refuse("canonical patch differs from worker evidence")
    return actual_paths

def publish(checkout, ev, publisher_sha, task_id):
    branch = f"change/{task_id}"
    if not re.fullmatch(r"change/[a-z0-9][a-z0-9-]{1,47}", branch):
        refuse("invalid publisher branch")
    current_sha = command("gh", "api", f"repos/{REPO}/git/ref/heads/main",
                          "--jq", ".object.sha")
    if current_sha != publisher_sha:
        refuse("main advanced; do not publish stale worker patch")
    # Fail closed on existing branch; never overwrite any previous task publication.
    remote = subprocess.run(["git", "-C", str(checkout), "ls-remote", "--exit-code",
                             "origin", f"refs/heads/{branch}"], capture_output=True)
    if remote.returncode != 2:
        refuse("branch exists or remote state cannot be verified")
    subprocess.run(["git", "-C", str(checkout), "switch", "-c", branch], check=True)
    # App installation tokens cannot impersonate a real user. Refuse
    # publication until an authorized human GitHub identity is available.
    name, email = github_user_identity(gh("user"))
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", name], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", email], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-m",
                    "test: update scoped regression fixture"], check=True)
    verify_commit(checkout, name, email)
    subprocess.run(["gh", "auth", "setup-git"], check=True)
    subprocess.run(["git", "-C", str(checkout), "push", "origin",
                    f"HEAD:refs/heads/{branch}"], check=True)
    title = "test: update scoped regression fixture"
    body = (
        "## Summary\n\n"
        "- Update the regression fixture covered by this change.\n\n"
        "## Validation\n\n"
        "- Review the repository checks before merging.\n"
    )
    command("gh", "pr", "create", "--repo", REPO, "--base", "main",
            "--head", branch, "--title", title, "--body", body)
    print("PUBLISH_OK", REPO, branch, ev["run_id"])

def main():
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"):
        refuse("publisher must execute on trusted main workflow_dispatch")
    publisher_sha = os.environ["GITHUB_SHA"]
    if not SHA_RE.fullmatch(publisher_sha):
        refuse("invalid publisher SHA")
    task_id = os.environ["FLEET_TASK_ID"]
    if not ID_RE.fullmatch(task_id):
        refuse("invalid task ID")
    run_raw = os.environ["FLEET_SOURCE_RUN_ID"]
    if not run_raw.isdecimal() or int(run_raw) < 1:
        refuse("invalid source run ID")
    run_id = int(run_raw)
    if run_id == int(os.environ["GITHUB_RUN_ID"]):
        refuse("cannot publish own run")
    run = gh(f"repos/{REPO}/actions/runs/{run_id}")
    wf = gh(f"repos/{REPO}/actions/workflows/{WORKFLOW}")
    attempt = verify_source_run(run, wf["id"], run_id, publisher_sha)
    jobs = gh(f"repos/{REPO}/actions/runs/{run_id}/jobs?per_page=100")
    if jobs.get("total_count") != 2:
        refuse("unexpected source job pagination")
    verify_jobs(jobs.get("jobs"))
    artifacts = gh(f"repos/{REPO}/actions/runs/{run_id}/artifacts?per_page=100")
    if artifacts.get("total_count", 0) > 100:
        refuse("unexpected source artifact pagination")
    name = verify_artifact_metadata(artifacts.get("artifacts"), run_id, attempt, task_id)
    root = Path(os.environ["RUNNER_TEMP"]) / "fleet-source"
    root.mkdir(exist_ok=False)
    subprocess.run(["gh", "run", "download", str(run_id), "--repo", REPO,
                    "--name", name, "--dir", str(root)], check=True)
    found = sorted(p.name for p in root.iterdir())
    if found != ["changes.patch", "result.json"]:
        refuse("unexpected artifact file layout")
    if any(p.is_symlink() for p in root.iterdir()):
        refuse("artifact symlink")
    result_path = root / "result.json"
    if result_path.stat().st_size > 8192:
        refuse("oversized evidence")
    ev = json.loads(result_path.read_text(encoding="utf-8"))
    patch = (root / "changes.patch").read_bytes()
    paths = verify_evidence(ev, patch, run_id, attempt, publisher_sha, task_id)
    checkout = Path(os.environ["GITHUB_WORKSPACE"]) / "target"
    verify_local_patch(checkout, root / "changes.patch", paths, publisher_sha,
                       ev["patch_sha256"])
    if os.environ.get("FLEET_PUBLISH_DRY_RUN", "false").lower() == "true":
        print("VERIFIED_ONLY", REPO, run_id, task_id, ev["patch_sha256"])
    else:
        publish(checkout, ev, publisher_sha, task_id)

if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, json.JSONDecodeError,
            subprocess.CalledProcessError, UnicodeError) as exc:
        raise SystemExit("Fleet publisher refused: " + type(exc).__name__ + ": " + str(exc)[:200])
