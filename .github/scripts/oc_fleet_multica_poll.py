#!/usr/bin/env python3
"""Bounded single-operator Multica -> GitHub worker poller.

Only pre-approved metadata in a pinned Multica project can be dispatched.
An atomic local claim and the controller's run receipt prevent blind retries.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import oc_fleet_multica as handoff
import oc_fleet_multica_finalize as finalizer


def ready_issues(project):
    p = subprocess.run([
        "multica", "issue", "list", "--project", project,
        "--metadata", "fleet_ready=true", "--limit", "100",
        "--fields", "identifier,status,project_id,assignee_type,metadata",
        "--output", "json"
    ], capture_output=True, text=True, check=True, timeout=40)
    result = json.loads(p.stdout)
    if not isinstance(result, dict) or result.get("has_more") is not False:
        raise ValueError("Incomplete Multica issue listing")
    issues = result.get("issues")
    if not isinstance(issues, list) or len(issues) > 100:
        raise ValueError("Invalid Multica issue listing")
    return issues


WORKER_STATUSES = ("queued","in_progress","waiting","requested")
MAX_CONTROLLER_PARALLEL = 20


def worker_slots(worker_repo, budget):
    """Admit only free workflow slots using current GitHub Actions run state.

    Counts active worker *runs*, not old job history; a single run has
    sequential validate and execute jobs. The configured budget is an
    operator ceiling, not a guessed GitHub plan entitlement.
    """
    if not handoff.REPO_RE.fullmatch(worker_repo):
        raise ValueError("Invalid worker repo for capacity query")
    if type(budget) is not int or not 1 <= budget <= MAX_CONTROLLER_PARALLEL:
        raise ValueError("Unapproved worker capacity")
    busy=0
    for status in WORKER_STATUSES:
        result=subprocess.run([
            "gh","api",f"repos/{worker_repo}/actions/workflows/"
            f"oc-fleet-worker.yml/runs?status={status}&per_page=100"],
            text=True,capture_output=True,check=True,timeout=20)
        data=json.loads(result.stdout)
        if not isinstance(data,dict) or type(data.get("total_count")) is not int:
            raise ValueError("Worker run count unavailable")
        runs=data.get("workflow_runs")
        if not isinstance(runs,list) or len(runs) != data["total_count"] or len(runs) >= 100:
            raise ValueError("Incomplete worker capacity evidence")
        busy += data["total_count"]
    return max(0,budget-busy)


def file_scope(meta):
    """Return validated, case-normalized file/directory scopes in one target repo."""
    repo=meta["fleet_target_repo"].lower()
    paths=[meta["fleet_allowed_path"],*handoff.extra_allowed_paths(meta)]
    if any(type(path) is not str or not handoff.clean_relpath(
            path,directory=path.endswith("/")) for path in paths):
        raise ValueError("Invalid reserved worker file scope")
    return repo, tuple(path.casefold() for path in paths)


def file_scopes_overlap(a,b):
    """Exact files and directory descendants conflict, but siblings do not."""
    if a[0] != b[0]:
        return False
    for left in a[1]:
        for right in b[1]:
            if (left == right
                    or (left.endswith("/") and right.startswith(left))
                    or (right.endswith("/") and left.startswith(right))):
                return True
    return False


# One bounded read-only GitHub GraphQL query replaces an N+1 REST PR walk.
# Incomplete pagination means the safety decision cannot be made.
GITHUB_OPEN_PR_FILES_QUERY = (
    "query($owner:String!,$name:String!){"
    "repository(owner:$owner,name:$name){"
    "pullRequests(states:OPEN,first:100){pageInfo{hasNextPage}"
    "nodes{files(first:100){pageInfo{hasNextPage}nodes{path}}}}}}"
)


def safe_observed_github_file_path(value):
    """GitHub may list dotfiles; do not use worker write-authorization rules."""
    return (type(value) is str and 1 <= len(value) <= 4096
            and not value.startswith("/") and not value.endswith("/")
            and "\\" not in value and "\x00" not in value
            and all(part not in ("", ".", "..") for part in value.split("/")))


def open_github_pr_file_scopes(repo):
    """Paths held by any open PR, including Renovate/Dependabot, in one repo."""
    if not handoff.REPO_RE.fullmatch(repo):
        raise ValueError("Invalid GitHub scope repository")
    owner, name = repo.split("/")
    response=subprocess.run([
        "gh","api","graphql","-f",f"query={GITHUB_OPEN_PR_FILES_QUERY}",
        "-f",f"owner={owner}","-f",f"name={name}",
    ],capture_output=True,text=True,check=True,timeout=35)
    doc=json.loads(response.stdout)
    if type(doc) is not dict or doc.get("errors"):
        raise ValueError("Incomplete GitHub PR file inventory")
    repository=(doc.get("data") or {}).get("repository")
    if type(repository) is not dict:
        raise ValueError("Missing GitHub repository file inventory")
    connection=repository.get("pullRequests")
    if (type(connection) is not dict or type(connection.get("pageInfo")) is not dict
            or connection["pageInfo"].get("hasNextPage") is not False
            or type(connection.get("nodes")) is not list
            or len(connection["nodes"]) > 100):
        raise ValueError("Incomplete GitHub open PR inventory")
    occupied=[]
    for pr in connection["nodes"]:
        if type(pr) is not dict or type(pr.get("files")) is not dict:
            raise ValueError("Invalid GitHub PR file listing")
        files=pr["files"]
        if (type(files.get("pageInfo")) is not dict
                or files["pageInfo"].get("hasNextPage") is not False
                or type(files.get("nodes")) is not list or len(files["nodes"]) > 100):
            raise ValueError("Incomplete GitHub PR file listing")
        paths=[]
        for node in files["nodes"]:
            if (type(node) is not dict
                    or type(node.get("path")) is not str
                    or not safe_observed_github_file_path(node["path"])):
                raise ValueError("Unsafe GitHub PR file path")
            paths.append(node["path"].casefold())
        if paths:
            occupied.append((repo.lower(),tuple(paths)))
    return occupied


def occupied_file_scopes(project):
    """Reserve paths from active workers and unmerged fleet PRs; fail on partial inventory."""
    found=subprocess.run([
        "multica","issue","list","--project",project,"--limit","100",
        "--fields","identifier,status,metadata","--output","json"
    ],capture_output=True,text=True,check=True,timeout=40)
    data=json.loads(found.stdout)
    if (type(data) is not dict or data.get("has_more") is not False
            or type(data.get("issues")) is not list or len(data["issues"]) > 100):
        raise ValueError("Incomplete active file-scope inventory")
    occupied=[]
    for issue in data["issues"]:
        if type(issue) is not dict:
            raise ValueError("Invalid active task")
        if issue.get("status") not in ("in_progress","in_review"):
            continue
        meta=issue.get("metadata")
        if type(meta) is not dict:
            raise ValueError("Incomplete active task metadata")
        if meta.get("fleet_result") not in ("worker_dispatched","pr_created"):
            continue
        occupied.append(file_scope(meta))
    return occupied


def controller_receipt(meta):
    identity = meta["fleet_target_repo"].lower() + "#" + meta["fleet_target_issue"]
    name = hashlib.sha256(identity.encode()).hexdigest()[:24] + ".json"
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ocfilo-fleet" / name


def dispatch_one(item, project, execute, claim_dir):
    key = item.get("identifier")
    if not isinstance(key, str) or not handoff.ISSUE_RE.fullmatch(key):
        raise ValueError("Invalid Multica issue in queue")
    fresh = handoff.read_issue(key)
    meta = handoff.validate_issue(fresh, key, project, execute)
    if controller_receipt(meta).exists():
        print("SKIP_EXISTING_RECEIPT", key)
        return "existing"
    claim = claim_dir / (key + ".claim")
    if claim.exists():
        print("SKIP_EXISTING_CLAIM", key)
        return "claimed"
    if not execute:
        print("PLAN", key, meta["fleet_worker_repo"])
        return "planned"
    claim_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with claim.open("x", encoding="utf-8") as output:
            output.write(key + "\n")
    except FileExistsError:
        print("SKIP_EXISTING_CLAIM", key)
        return "claimed"
    cmd = handoff.controller_command(key, meta, True)
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=2000)
    if result.returncode:
        raise ValueError(f"Controller run failed for {key}; claim retained")
    found = []
    for line in result.stdout.splitlines():
        if line.startswith('{"status": "PR_CREATED"'):
            found.append(json.loads(line))
    if len(found) != 1 or found[0].get("status") != "PR_CREATED":
        raise ValueError(f"Publication unverified for {key}; claim retained")
    run = found[0].get("run_id")
    if not isinstance(run, str) or not run.isdecimal():
        raise ValueError(f"Invalid run receipt for {key}")
    pr_url = found[0].get("url")
    match = (re.fullmatch(
        r"https://github\.com/" + re.escape(meta["fleet_target_repo"]) + r"/pull/([1-9][0-9]*)",
        pr_url or "") if isinstance(pr_url,str) else None)
    if not match:
        raise ValueError(f"Invalid PR URL receipt for {key}")
    pr_number = match.group(1)
    for argv in (
        ["issue", "metadata", "set", key, "--key", "fleet_ready", "--value", "false", "--type", "bool"],
        ["issue", "metadata", "set", key, "--key", "fleet_result", "--value", "pr_created", "--type", "string"],
        ["issue", "metadata", "set", key, "--key", "fleet_run_id", "--value", run, "--type", "string"],
        ["issue", "metadata", "set", key, "--key", "fleet_pull_request", "--value", pr_number, "--type", "string"],
        ["issue", "status", key, "in_review", "--no-start"],
    ):
        subprocess.run(["multica", *argv], capture_output=True, text=True,
                       check=True, timeout=25)
    print("PR_CREATED", key, flush=True)
    return "dispatched"


def poll(project, execute, claim_dir, max_parallel=1, dispatch_handler=None):
    if type(max_parallel) is not int or not 1 <= max_parallel <= MAX_CONTROLLER_PARALLEL:
        raise ValueError("Configured parallel limit must be 1..20")
    issues = ready_issues(project)
    eligible = sorted(
        (x for x in issues if x.get("status") in ("backlog", "todo")
         and x.get("metadata", {}).get("fleet_ready") is True),
        key=lambda x: str(x.get("identifier", "")),
    )
    print("ELIGIBLE", len(eligible), flush=True)
    if not eligible:
        return "empty"
    selected = []
    seen_targets = set()
    worker_available = {}
    # An account/repository-specific budget alone can exceed the fleet-wide
    # free ceiling after multiple cycles. Enforce one shared pool budget.
    # Read-only plans do not query worker pools or mutate reservation state.
    if execute:
        import oc_fleet_pool
        state = oc_fleet_pool.snapshot()
        global_available = min(max_parallel, state["global_free"])
        per_account_limit = state["per_account_limit"]
    else:
        global_available = max_parallel
        per_account_limit = max_parallel
    # Only inspect active reservations when there is a genuinely new task.
    # Plan mode and already-claimed/received work never make this extra query.
    occupied = None if execute else []
    # Only query GitHub PRs for target repositories with genuinely new work.
    # This includes external bot PRs, absent from the Multica task list.
    github_pr_scopes = {}
    for item in eligible:
        key = item.get("identifier")
        if not isinstance(key, str) or not handoff.ISSUE_RE.fullmatch(key):
            raise ValueError("Invalid issue identity in ready list")
        fresh = handoff.read_issue(key)
        meta = handoff.validate_issue(fresh, key, project, execute)
        if controller_receipt(meta).exists():
            print("SKIP_EXISTING_RECEIPT", key)
            continue
        if (claim_dir / (key + ".claim")).exists():
            print("SKIP_EXISTING_CLAIM", key)
            continue
        target = (meta["fleet_target_repo"].lower(), meta["fleet_target_issue"])
        if target in seen_targets:
            print("SKIP_DUPLICATE_TARGET", key)
            continue
        if occupied is None:
            occupied=occupied_file_scopes(project)
        scope=file_scope(meta)
        repo=scope[0]
        if repo not in github_pr_scopes and execute:
            github_pr_scopes[repo]=open_github_pr_file_scopes(meta["fleet_target_repo"])
        if any(file_scopes_overlap(scope,held)
               for held in [*occupied,*github_pr_scopes.get(repo,[])]):
            print("SKIP_OVERLAPPING_PATH", key,flush=True)
            continue
        worker_repo=meta["fleet_worker_repo"]
        if execute:
            if global_available <= 0:
                print("SKIP_GLOBAL_CAPACITY", key, flush=True)
                continue
            if worker_repo not in worker_available:
                worker_available[worker_repo]=worker_slots(
                    worker_repo, min(max_parallel, per_account_limit))
            if worker_available[worker_repo] <= 0:
                print("SKIP_WORKER_AT_CAPACITY",key,worker_repo,flush=True)
                continue
            worker_available[worker_repo]-=1
            global_available-=1
        seen_targets.add(target)
        occupied.append(scope)
        selected.append(fresh)
        if len(selected) == max_parallel:
            break
    if not selected:
        if global_available <= 0 or any(v == 0 for v in worker_available.values()):
            return "no_capacity"
        return "no_new_work"
    if not execute:
        for item in selected:
            print("PLAN", item["identifier"], item["metadata"]["fleet_worker_repo"])
        return "planned"
    handler=dispatch_handler or dispatch_one
    if len(selected) == 1:
        return handler(selected[0], project, execute, claim_dir)
    errors = []
    with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="oc-fleet") as pool:
        futures = {
            pool.submit(handler, item, project, True, claim_dir): item["identifier"]
            for item in selected
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result = future.result()
                print("WORKER_RESULT", key, result, flush=True)
            except Exception as exc:
                errors.append(key)
                print("WORKER_BLOCKED", key, type(exc).__name__, flush=True)
    if errors:
        raise ValueError("Parallel run needs reconciliation for: " + ",".join(errors))
    return "dispatched_parallel"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project-id", required=True)
    p.add_argument("--run", action="store_true")
    p.add_argument("--max-parallel", type=int, choices=range(1,MAX_CONTROLLER_PARALLEL+1), default=1)
    p.add_argument("--repair-pilot-pr2", action="store_true",
                   help="Owner opt-in: repair only pinned F1 PR #2 from trusted CI evidence")
    p.add_argument("--auto-codacy-bare-assert",action="store_true",
                   help="Opt in only verified Codacy bare-assert test-file repair on ADXL355")
    p.add_argument("--detached",action="store_true",
                   help="Persist worker run and reconcile completed jobs in later timer cycles")
    p.add_argument("--claim-dir", type=Path, default=Path.home() / ".local/state/oc-fleet/claims")
    args = p.parse_args()
    if not handoff.UUID_RE.fullmatch(args.project_id):
        raise ValueError("Invalid project")
    if args.detached:
        import oc_fleet_multica_detached
        oc_fleet_multica_detached.cycle(args.project_id,args.run,
                                       args.claim_dir,args.max_parallel)
    else:
        finalizer.poll(args.project_id, args.run, controller_receipt)
        poll(args.project_id, args.run, args.claim_dir, args.max_parallel)
    if args.repair_pilot_pr2:
        import oc_fleet_pr_repair
        oc_fleet_pr_repair.execute_once(
            Path.home() / ".local/state/oc-fleet/f1-repairs.sqlite3",
            dry_run=not args.run,
        )
    if args.auto_codacy_bare_assert:
        import oc_fleet_auto_repair
        try:
            oc_fleet_auto_repair.poll(
                Path.home()/".local/state/oc-fleet/auto-policies",
                Path.home()/".local/state/oc-fleet/scoped-repairs.sqlite3",
                live=args.run,
            )
        except (ValueError, OSError, subprocess.SubprocessError,
                json.JSONDecodeError, KeyError, TypeError) as err:
            # The optional production selector must not halt F1 or issue dispatch.
            # Existing durable reservations remain for owner reconciliation.
            print("AUTO_REPAIR_BLOCKED", type(err).__name__,
                  str(err)[:160], file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError,
            json.JSONDecodeError) as err:
        print("FLEET_POLL_BLOCKED:", str(err)[:180], file=sys.stderr)
        sys.exit(1)
