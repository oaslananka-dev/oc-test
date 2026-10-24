#!/usr/bin/env python3
"""Single operator entrypoint: plan/submit a GitHub issue, list and track Fleet.

No worker or PR is directly dispatched here. The existing bounded detached
controller is the only consumer of fleet_ready Multica tasks.
"""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import oc_fleet_multica as contract
from oc_fleet_contract import clean_relpath, AGENTS, MODELS, PROFILES

PROJECT = "0506e0e5-01a7-4de9-b63b-67e993809721"
WORKER_REPO = "MakerParsDev/oc-test"
WORKER_ACCOUNT = "MakerParsDev"
LOCK = Path.home() / ".local/state/oc-fleet/dispatch.lock"
PAGE = 100
LIMIT = 1000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(argv, input_text=None):
    result = subprocess.run(
        argv, input=input_text, text=True, capture_output=True,
        timeout=60, check=True,
    )
    return result.stdout


def multica(*args, input_text=None):
    return run(["multica", "issue", *args], input_text=input_text)


def validate_inputs(repo, issue, paths, model, profile, agent):
    require(type(repo) is str and bool(contract.REPO_RE.fullmatch(repo)),
            "Invalid GitHub repository")
    require(repo.lower() != "oaslananka-dev/oc-test",
            "Pilot repository uses a different merge contract")
    require(type(issue) is int and 1 <= issue <= 999999999,
            "Invalid GitHub issue number")
    require(type(paths) is list and 1 <= len(paths) <= 12,
            "Explicit approval for 1..12 source paths is required")
    require(all(type(p) is str and
                clean_relpath(p, directory=p.endswith("/")) for p in paths),
            "Unsafe source path")
    require(len({p.casefold() for p in paths}) == len(paths),
            "Duplicate source path")
    require(not any(a.endswith("/") and b.casefold().startswith(a.casefold())
                    for a in paths for b in paths if a != b),
            "Overlapping source paths")
    require(model in MODELS and (
        model.endswith("-free") or model == "opencode/big-pickle"
    ), "Unapproved or paid model")
    require(profile in PROFILES and agent in AGENTS,
            "Unapproved worker profile or agent")
    return paths


def preview(repo, issue, paths, model, profile, agent):
    validate_inputs(repo, issue, paths, model, profile, agent)
    # Use the *same* admission path as production worker dispatch.
    # --require-approved-target disallows auto-whitelisting repositories.
    argv = [
        sys.executable, str(Path(__file__).with_name("oc_fleet_issue.py")),
        "run", "--repo", repo, "--issue", str(issue),
        "--worker-repo", WORKER_REPO, "--worker-account", WORKER_ACCOUNT,
        "--target-account", repo.split("/")[0],
        "--model", model, "--profile", profile, "--agent", agent,
        "--external-pr-only", "--require-approved-target", "--dry-run",
    ]
    for path in paths:
        argv.extend(["--path", path])
    result = run(argv)
    # No issue or state is created until the pinned production preflight passes.
    docs = [json.loads(result[result.index("{"):])] if "{" in result else []
    require(len(docs) == 1 and docs[0].get("status") == "DRY_RUN"
            and docs[0].get("repo", "").lower() == repo.lower()
            and docs[0].get("issue") == issue
            and docs[0].get("scope") == paths,
            "Controller preflight evidence missing or inconsistent")
    return {
        "state": "ready_to_submit", "repo": repo, "github_issue": issue,
        "paths": paths, "model": model, "profile": profile, "agent": agent,
        "base_sha": docs[0]["sha"], "worker_repo": WORKER_REPO,
        "project_id": PROJECT, "auto_merge": False,
        "dispatch": "bounded scheduled controller, not immediate",
    }


def list_raw():
    all_issues = []
    for offset in range(0, LIMIT, PAGE):
        page = json.loads(multica(
            "list", "--project", PROJECT, "--limit", str(PAGE),
            "--offset", str(offset), "--fields",
            "identifier,status,metadata,title,project_id", "--output", "json",
        ))
        require(type(page) is dict and type(page.get("issues")) is list
                and type(page.get("has_more")) is bool
                and len(page["issues"]) <= PAGE, "Incomplete task list")
        all_issues.extend(page["issues"])
        if not page["has_more"]:
            return all_issues
        require(len(page["issues"]) == PAGE, "Invalid task pagination")
    raise ValueError("Task inventory exceeds safety bound")


def duplicates(items, repo, github_issue):
    matches = []
    for item in items:
        meta = item.get("metadata") or {}
        if (type(meta) is dict
                and str(meta.get("fleet_target_repo", "")).lower() == repo.lower()
                and str(meta.get("fleet_target_issue", "")) == str(github_issue)):
            matches.append(item.get("identifier"))
    return matches


def update(key, field, value, kind="string"):
    require(bool(contract.ISSUE_RE.fullmatch(key)), "Invalid Multica key")
    require(field in {
        "fleet_target_repo", "fleet_target_issue", "fleet_target_account",
        "fleet_worker_repo", "fleet_worker_account", "fleet_allowed_path",
        "fleet_allowed_paths", "fleet_model", "fleet_profile", "fleet_agent",
        "fleet_auto_merge", "fleet_external_pr_only", "fleet_ready",
    }, "Unapproved routing field")
    multica("metadata", "set", key, "--key", field, "--value", value,
            "--type", kind)


def task_view(issue):
    meta = issue.get("metadata") or {}
    require(type(meta) is dict, "Invalid task metadata")
    return {
        "task": issue.get("identifier"), "status": issue.get("status"),
        "title": issue.get("title"),
        "repo": meta.get("fleet_target_repo"),
        "github_issue": meta.get("fleet_target_issue"),
        "worker_run": meta.get("fleet_run_id"),
        "pull_request": meta.get("fleet_pull_request"),
        "result": meta.get("fleet_result"),
        "ready": meta.get("fleet_ready"),
        "merge_sha": meta.get("fleet_merge_sha"),
        "superseded_by": meta.get("fleet_superseded_by_pr"),
    }


def submit(opts):
    lock = LOCK
    require(lock.parent.is_dir() and not lock.is_symlink(),
            "Trusted dispatcher lock directory missing")
    with lock.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise ValueError("Dispatcher active, retry when lock is free") from err
        # Keep the admission decision and queued task creation serialized.
        proof = preview(opts.repo, opts.issue, opts.path,
                        opts.model, opts.profile, opts.agent)
        found = duplicates(list_raw(), opts.repo, opts.issue)
        require(not found, "Existing Fleet task for this GitHub issue: " +
                ",".join(str(x) for x in found))
        title = "OC Fleet: " + opts.repo + "#" + str(opts.issue)
        description = (
            "GitHub source issue: https://github.com/" + opts.repo
            + "/issues/" + str(opts.issue) + "\n"
            "Trusted OpenCode GitHub-hosted worker; PR-only; manual merge.\n"
            "Scopes: " + json.dumps(opts.path, ensure_ascii=False)
        )
        created = json.loads(multica(
            "create", "--project", PROJECT, "--status", "todo",
            "--title", title, "--description-stdin", "--output", "json",
            input_text=description,
        ))
        key = created.get("identifier")
        if key is None and type(created.get("issue")) is dict:
            key = created["issue"].get("identifier")
        require(type(key) is str and contract.ISSUE_RE.fullmatch(key),
                "Created issue ID unconfirmed; inspect Multica before retry")
        # All metadata writes leave fleet_ready absent until the very last step.
        # Existing dispatcher ignores this incomplete task.
        try:
            strings = {
                "fleet_target_repo": opts.repo,
                "fleet_target_issue": str(opts.issue),
                "fleet_target_account": opts.repo.split("/")[0],
                "fleet_worker_repo": WORKER_REPO,
                "fleet_worker_account": WORKER_ACCOUNT,
                "fleet_allowed_path": opts.path[0],
                "fleet_allowed_paths": json.dumps(opts.path[1:], separators=(",", ":")),
                "fleet_model": opts.model,
                "fleet_profile": opts.profile,
                "fleet_agent": opts.agent,
            }
            for field, value in strings.items():
                update(key, field, value)
            update(key, "fleet_auto_merge", "false", "bool")
            update(key, "fleet_external_pr_only", "true", "bool")
            update(key, "fleet_ready", "false", "bool")
            fresh = contract.read_issue(key)
            contract.validate_issue(fresh, key, PROJECT, False)
            require(fresh.get("status") == "todo"
                    and fresh.get("metadata", {}).get("fleet_ready") is False,
                    "Task changed during admission")
            # Confirm all exact routing fields match the operator's plan.
            actual = fresh["metadata"]
            require(all(actual.get(k) == v for k, v in strings.items())
                    and actual.get("fleet_auto_merge") is False
                    and actual.get("fleet_external_pr_only") is True,
                    "Staged task differs from approved routing")
            update(key, "fleet_ready", "true", "bool")
        except Exception as err:
            raise ValueError(
                "Task " + key + " may be partially staged and must be "
                "inspected manually before retry: " + str(err)[:160]
            ) from err
        completed = contract.read_issue(key)
        require(completed.get("status") == "todo"
                and completed.get("metadata", {}).get("fleet_ready") is True,
                "Task created but final admission readback ambiguous")
        return {"state": "queued", **task_view(completed),
                "base_sha_at_preflight": proof["base_sha"],
                "dispatch": "next eligible controller cycle"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "submit"):
        cmd = subs.add_parser(name, help="Preflight only / enqueue verified issue")
        cmd.add_argument("--repo", required=True)
        cmd.add_argument("--issue", required=True, type=int)
        cmd.add_argument("--path", action="append", required=True)
        cmd.add_argument("--model", default="opencode/big-pickle")
        cmd.add_argument("--profile", default="base")
        cmd.add_argument("--agent", default="build")
        if name == "submit":
            cmd.add_argument("--confirm-target", required=True,
                             help="Exact OWNER/REPO#ISSUE, required for writes")
    status = subs.add_parser("status", help="Show one Multica task")
    status.add_argument("task")
    subs.add_parser("list", help="Summarize project tasks")
    args = parser.parse_args(argv)
    if args.command in ("plan", "submit"):
        validate_inputs(args.repo, args.issue, args.path, args.model,
                        args.profile, args.agent)
        if args.command == "submit":
            require(args.confirm_target == args.repo + "#" + str(args.issue),
                    "Explicit exact repository and issue confirmation required")
            result = submit(args)
        else:
            result = preview(args.repo, args.issue, args.path, args.model,
                             args.profile, args.agent)
    elif args.command == "status":
        require(bool(contract.ISSUE_RE.fullmatch(args.task)), "Invalid task ID")
        d = contract.read_issue(args.task)
        require(d.get("project_id") == PROJECT, "Task belongs to another project")
        result = task_view(d)
    else:
        issues = [task_view(d) for d in list_raw()
                  if type(d.get("metadata")) is dict
                  and d["metadata"].get("fleet_target_repo")]
        result = {"count": len(issues), "tasks": issues}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError,
            json.JSONDecodeError) as e:
        print(json.dumps({"state": "blocked", "reason": str(e)[:280]}),
              file=sys.stderr)
        raise SystemExit(2)
