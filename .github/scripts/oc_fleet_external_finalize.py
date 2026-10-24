#!/usr/bin/env python3
"""Explicit, fail-closed post-merge reconciliation of an external Fleet PR.

Not a dispatcher, merger, unattended finalizer or owner authorization.
Dry-run is the default. Never invoked by the systemd fleet timer.
"""
import argparse
import json
import re
import subprocess

import oc_fleet_external_base_proof as base_proof
import oc_fleet_multica as handoff
import oc_fleet_multica_detached as detached
import oc_fleet_multica_poll as poller

SHA = re.compile(r"[0-9a-f]{40}\Z")
NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")
PILOT = "oaslananka-dev/oc-test"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(*args):
    return subprocess.run(
        args, capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()


def api(route):
    return json.loads(command("gh", "api", route))


def exact_sha(value):
    return type(value) is str and bool(SHA.fullmatch(value))


def get_task(key, project, allowed_statuses=("in_review", "done")):
    require(type(key) is str and handoff.ISSUE_RE.fullmatch(key),
            "Invalid task key")
    require(type(project) is str and handoff.UUID_RE.fullmatch(project),
            "Invalid project")
    issue = handoff.read_issue(key)
    require(issue.get("identifier") == key and issue.get("project_id") == project,
            "Untrusted Multica identity")
    m = issue.get("metadata")
    require(type(m) is dict, "Missing metadata")
    require(issue.get("assignee_type") != "agent"
            and issue.get("status") in allowed_statuses,
            "Unexpected Multica issue state")
    require(type(m.get("fleet_target_repo")) is str, "Bad target repository")
    require(m.get("fleet_external_pr_only") is True
            and m.get("fleet_auto_merge") is False
            and m.get("fleet_ready") is False
            and m.get("fleet_target_repo", "").lower() != PILOT,
            "Not an explicitly authorized external manual-review task")
    for field in ("fleet_target_repo", "fleet_worker_repo"):
        require(type(m.get(field)) is str
                and handoff.REPO_RE.fullmatch(m[field]), "Bad repository")
    for field in ("fleet_run_id", "fleet_target_issue", "fleet_pull_request"):
        require(type(m.get(field)) is str and NUMBER.fullmatch(m[field]),
                "Invalid run/issue/PR identity")
    require(type(m.get("fleet_allowed_path")) is str
            and handoff.clean_relpath(
                m["fleet_allowed_path"],
                directory=m["fleet_allowed_path"].endswith("/")),
            "Invalid scope")
    handoff.selected_agent_configuration(m)
    handoff.selected_agent_role(m)
    paths = [m["fleet_allowed_path"], *handoff.extra_allowed_paths(m)]
    require(len(paths) == len({x.casefold() for x in paths})
            and not any(
                a != b and a.endswith("/") and b.startswith(a)
                for a in paths for b in paths),
            "Overlapping authorized paths")
    return issue, m, paths


def original_receipt(m, key):
    path = poller.controller_receipt(m)
    require(path.is_file() and not path.is_symlink()
            and 0 < path.stat().st_size <= 24000, "No trustworthy receipt")
    doc = detached.receipt_for(m)
    manifest = doc["manifest"]
    require(manifest.get("multica_issue") == key
            and manifest.get("base_branch") == "main"
            and exact_sha(manifest.get("checkout_sha"))
            and type(manifest.get("task_id")) is str
            and bool(manifest["task_id"])
            and type(doc.get("run_id")) is int
            and str(doc["run_id"]) == m["fleet_run_id"],
            "Receipt identity or source SHA mismatch")
    # The original trusted receipt already pins the independently approved
    # base-advance policy to this task's immutable metadata.
    return manifest


def worker_success(m):
    repo = m["fleet_worker_repo"]
    run = api(f"repos/{repo}/actions/runs/{m['fleet_run_id']}")
    require(run.get("id") == int(m["fleet_run_id"])
            and (run.get("repository") or {}).get("full_name", "").lower()
                == repo.lower()
            and run.get("event") == "workflow_dispatch"
            and run.get("run_attempt") == 1
            and run.get("head_branch") == "main"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and type(run.get("path")) is str
            and run["path"].split("@", 1)[0] ==
                ".github/workflows/oc-fleet-worker.yml",
            "Worker execution was not verified successful")


def trusted_pr(m, paths, source_sha, allow_advance=False):
    repo = m["fleet_target_repo"]
    number = m["fleet_pull_request"]
    pr = api(f"repos/{repo}/pulls/{number}")
    require(pr.get("number") == int(number)
            and (pr.get("head") or {}).get("repo", {}).get("full_name", "").lower()
                == repo.lower()
            and (pr.get("base") or {}).get("repo", {}).get("full_name", "").lower()
                == repo.lower()
            and (pr.get("base") or {}).get("ref") == "main",
            "PR identity/branch mismatch")
    require(re.search(r"(?i)\bcloses\s+#" +
                      re.escape(m["fleet_target_issue"]) + r"\b",
                      pr.get("body") or ""), "Wrong PR issue binding")
    if not pr.get("merged"):
        require(pr.get("state") == "open", "Unmerged PR in unexpected state")
        return None
    require(pr.get("state") == "closed"
            and type(pr.get("merged_at")) is str
            and bool(pr["merged_at"])
            and exact_sha(pr.get("merge_commit_sha")),
            "Unverified merged PR")
    head = (pr.get("head") or {}).get("sha")
    require(exact_sha(head), "Unverified PR head")
    commits = api(f"repos/{repo}/pulls/{number}/commits?per_page=100")
    require(type(commits) is list and len(commits) == 1
            and commits[0].get("sha") == head,
            "Unexpected PR commit provenance")
    commit = api(f"repos/{repo}/git/commits/{head}")
    parents = commit.get("parents")
    require(type(parents) is list and len(parents) == 1
            and exact_sha(parents[0].get("sha")),
            "PR source commit has untrusted ancestry")
    actual_parent = parents[0]["sha"]
    if actual_parent != source_sha:
        require(allow_advance is True,
                "Original source SHA moved without explicit authorization")
        base_proof.validate(repo, source_sha, actual_parent, paths, api)
    changed = api(f"repos/{repo}/pulls/{number}/files?per_page=100")
    require(type(changed) is list and 0 < len(changed) < 100
            and all(type(f) is dict and type(f.get("filename")) is str
                    and f.get("status") in ("added", "modified") for f in changed),
            "Unsafe or incomplete PR file inventory")
    observed = {f["filename"].casefold() for f in changed}
    require(len(observed) == len(changed)
            and observed <= {p.casefold() for p in paths},
            "PR changed files outside immutable scope")
    return pr, head


def required_checks(repo, sha):
    # Effective GitHub branch rulesets (repository AND organization).
    rules = api(f"repos/{repo}/rules/branches/main?per_page=100")
    require(type(rules) is list and len(rules) < 100,
            "Missing or incomplete effective branch protection rules")
    required = set()
    for rule in rules:
        require(type(rule) is dict, "Malformed branch rules")
        if rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters") or {}
        contexts = params.get("required_status_checks")
        require(type(contexts) is list and contexts, "Missing required checks")
        for check in contexts:
            context = check.get("context") if type(check) is dict else None
            require(type(context) is str and context.strip(),
                    "Malformed required check")
            required.add(context)
    require(required, "No verifiable required security and CI policy")

    data = api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    checks = data.get("check_runs") if type(data) is dict else None
    require(type(checks) is list and checks and
            type(data.get("total_count")) is int
            and data["total_count"] == len(checks) and len(checks) < 100,
            "Incomplete check run evidence")
    observed = {}
    for check in checks:
        name = check.get("name")
        require(type(name) is str and name not in observed
                and check.get("head_sha") == sha
                and check.get("status") == "completed"
                and check.get("conclusion") in ("success", "neutral", "skipped"),
                "Check run failed, incomplete or ambiguous")
        observed[name] = check["conclusion"]
    status = api(f"repos/{repo}/commits/{sha}/status?per_page=100")
    contexts = status.get("statuses") if type(status) is dict else None
    require(type(contexts) is list
            and type(status.get("total_count")) is int
            and status["total_count"] == len(contexts) and len(contexts) < 100
            and (not contexts or status.get("state") == "success"),
            "Commit status failed or incomplete")
    for item in contexts:
        name = item.get("context")
        require(type(name) is str and name not in observed
                and item.get("state") == "success",
                "Commit status failed or duplicated a check run")
        observed[name] = "success"
    require(all(observed.get(name) == "success" for name in required),
            "Required GitHub CI/security checks not successful")
    return sorted(required)


def review(key, project):
    issue, m, paths = get_task(key, project)
    require(m.get("fleet_result") in ("pr_created", "completed"),
            "Unexpected previous Fleet result")
    manifest = original_receipt(m, key)
    worker_success(m)
    result = trusted_pr(m, paths, manifest["checkout_sha"],
                        allow_advance=m.get("fleet_allow_safe_base_advance") is True)
    if result is None:
        require(issue["status"] == "in_review"
                and m["fleet_result"] == "pr_created",
                "Contradictory Multica/PR state")
        return {"state": "waiting", "reason": "pr_not_merged", "issue": key}
    pr, sha = result
    required = required_checks(m["fleet_target_repo"], sha)
    github_issue = api(f"repos/{m['fleet_target_repo']}/issues/{m['fleet_target_issue']}")
    require(github_issue.get("number") == int(m["fleet_target_issue"])
            and github_issue.get("state") == "closed",
            "Target issue not closed")
    merge_sha = pr["merge_commit_sha"]
    if issue["status"] == "done":
        require(m.get("fleet_result") == "completed"
                and m.get("fleet_merge_sha") == merge_sha,
                "Done state lacks matching merge proof")
        state = "already_completed"
    else:
        require(issue["status"] == "in_review"
                and m.get("fleet_result") == "pr_created"
                and m.get("fleet_merge_sha") in (None, merge_sha),
                "Conflicting intermediate Multica state")
        state = "ready"
    return {"state": state, "issue": key, "pull_request": int(m["fleet_pull_request"]),
            "merge_sha": merge_sha, "verified_checks": required}


def apply(result, key, merge_sha):
    require(result["state"] == "ready"
            and result["issue"] == key
            and result["merge_sha"] == merge_sha,
            "Live mutation requires exact approved issue and SHA")
    # No GitHub merge, push, deploy, retry or controller dispatch here.
    # Preserve partial-update evidence for operator reconciliation on failure.
    command("multica", "issue", "metadata", "set", key,
            "--key", "fleet_merge_sha", "--value", merge_sha, "--type", "string")
    command("multica", "issue", "metadata", "set", key,
            "--key", "fleet_result", "--value", "completed", "--type", "string")
    command("multica", "issue", "status", key, "done", "--no-start")
    # A successful CLI return is not acceptance without authoritative readback.
    actual = handoff.read_issue(key)
    metadata = actual.get("metadata") or {}
    require(actual.get("identifier") == key
            and actual.get("status") == "done"
            and metadata.get("fleet_result") == "completed"
            and metadata.get("fleet_merge_sha") == merge_sha,
            "Post-write Multica acceptance unverified; manual reconciliation required")
    return "done"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Operator-authorized Multica-only state mutation")
    parser.add_argument("--confirm-issue", default="")
    parser.add_argument("--confirm-merge-sha", default="")
    args = parser.parse_args()
    try:
        # These checks precede ANY action, even dry-run.
        if args.apply:
            require(args.confirm_issue == args.issue
                    and exact_sha(args.confirm_merge_sha),
                    "Explicit run-specific mutation confirmation missing")
        result = review(args.issue, args.project_id)
        if args.apply:
            require(result["state"] == "ready", "Not ready to complete")
            apply(result, args.confirm_issue, args.confirm_merge_sha)
            result["state"] = "applied"
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError,
            json.JSONDecodeError) as err:
        print(json.dumps({"state": "blocked", "reason": str(err)[:180]}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
