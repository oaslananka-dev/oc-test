#!/usr/bin/env python3
"""Multica issue -> trusted Github Actions worker controller, without a Multica AI run.

The operator owns execution; issue metadata is a declared routing request, not a
credential or an independent grant to use arbitrary repositories.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from oc_fleet_contract import clean_relpath, MODELS, PROFILES, AGENTS

ISSUE_RE = re.compile(r"[A-Z][A-Z0-9]{1,14}-[1-9][0-9]{0,9}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z")
REPO_RE = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}\Z")
ACCOUNT_RE = re.compile(r"[A-Za-z0-9-]{1,39}\Z")
REQUIRED = (
    "fleet_target_repo", "fleet_target_issue", "fleet_worker_repo",
    "fleet_worker_account", "fleet_target_account", "fleet_allowed_path",
    "fleet_ready"
)


def read_issue(issue_key):
    result = subprocess.run(
        ["multica", "issue", "get", issue_key, "--output", "json"],
        capture_output=True, text=True, timeout=30, check=True
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("unexpected Multica issue response")
    return data


def extra_allowed_paths(meta):
    """Decode explicit extra file scopes from Multica primitive-only metadata.

    Native lists from existing contracts remain accepted. Multica V1 stores
    JSON arrays as strings; never treat a comma string or an invalid JSON
    value as authorization. The validated list is never written back.
    """
    extra = meta.get("fleet_allowed_paths", [])
    if type(extra) is str:
        if len(extra) > 4096:
            raise ValueError("Unsafe extra paths")
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError) as error:
            raise ValueError("Unsafe extra paths") from error
    if type(extra) is not list or len(extra) > 11 or any(
        not isinstance(path, str)
        or not clean_relpath(path, directory=path.endswith("/"))
        for path in extra
    ):
        raise ValueError("Unsafe extra paths")
    return extra


TOOL_PROFILE_RE = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
INTEGRATIONS = frozenset({"context7", "codacy", "sonarqube"})


def selected_agent_role(meta):
    agent = meta.get("fleet_agent", "build")
    if type(agent) is not str or agent not in AGENTS:
        raise ValueError("Unapproved fleet agent role")
    return agent


def selected_agent_configuration(meta):
    """Explicit, bounded per-task choices; no arbitrary model or shell args."""
    model = meta.get("fleet_model", "opencode/big-pickle")
    profile = meta.get("fleet_profile", "base")
    tool_profile = meta.get("fleet_tool_profile", "")
    if type(model) is not str or model not in MODELS:
        raise ValueError("Unapproved fleet model")
    if type(profile) is not str or profile not in PROFILES:
        raise ValueError("Unapproved fleet execution profile")
    if type(tool_profile) is not str or (
        tool_profile and not TOOL_PROFILE_RE.fullmatch(tool_profile)
    ):
        raise ValueError("Invalid fleet tool profile name")

    # Multica V1 stores arrays as primitive strings. Never parse comma
    # strings or coerce scalars into a broader integration authorization.
    integrations = meta.get("fleet_integrations", [])
    if type(integrations) is str:
        if len(integrations) > 256:
            raise ValueError("Invalid fleet integrations")
        try:
            integrations = json.loads(integrations)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid fleet integrations") from exc
    if (
        type(integrations) is not list
        or len(integrations) > 3
        or any(type(i) is not str or i not in INTEGRATIONS for i in integrations)
        or len(set(integrations)) != len(integrations)
    ):
        raise ValueError("Invalid fleet integrations")
    return model, profile, tool_profile, integrations


def validate_issue(issue, key, project_id, execute):
    if issue.get("identifier") != key or issue.get("project_id") != project_id:
        raise ValueError("issue identity/project mismatch")
    if issue.get("status") not in ("backlog", "todo", "in_progress"):
        raise ValueError("issue status is not dispatchable")
    if issue.get("assignee_type") == "agent":
        raise ValueError("issue already assigned to native agent runtime")
    meta = issue.get("metadata")
    if not isinstance(meta, dict) or not all(k in meta for k in REQUIRED):
        raise ValueError("incomplete trusted routing metadata")
    target, worker = meta["fleet_target_repo"], meta["fleet_worker_repo"]
    if (not isinstance(target, str) or not REPO_RE.fullmatch(target)
            or not isinstance(worker, str) or not REPO_RE.fullmatch(worker)):
        raise ValueError("invalid routing repository")
    github_issue = meta["fleet_target_issue"]
    if not isinstance(github_issue, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", github_issue):
        raise ValueError("invalid target GitHub issue")
    for name in ("fleet_worker_account", "fleet_target_account"):
        v = meta[name]
        if not isinstance(v, str) or not ACCOUNT_RE.fullmatch(v):
            raise ValueError("invalid authorized account")
    path = meta["fleet_allowed_path"]
    if not isinstance(path,str) or not clean_relpath(
            path,directory=path.endswith("/")):
        raise ValueError("Unsafe Multica allowed path")
    # Additional paths are explicit rather than automatically inferred
    # from repository data, bot comments, or issue prose.
    extra=extra_allowed_paths(meta)
    paths=[path,*extra]
    if len({x.casefold() for x in paths}) != len(paths):
        raise ValueError("Duplicate or overlapping scope")
    if any(a != b and a.endswith("/") and b.startswith(a)
           for a in paths for b in paths):
        raise ValueError("Overlapping directory scope")
    if meta.get("fleet_auto_merge",False) is True and target.lower() != "oaslananka-dev/oc-test":
        raise ValueError("Automerge only supported for the pilot repository")
    if type(meta.get("fleet_external_pr_only",False)) is not bool:
        raise ValueError("External PR-only switch must be boolean")
    if target.lower() != "oaslananka-dev/oc-test" and meta.get("fleet_external_pr_only") is not True:
        raise ValueError("Non-pilot repo requires external PR-only authorization")
    selected_agent_configuration(meta)
    selected_agent_role(meta)
    safe_advance=meta.get("fleet_allow_safe_base_advance",False)
    if type(safe_advance) is not bool:
        raise ValueError("Safe base advance must be a boolean")
    if safe_advance and (meta.get("fleet_external_pr_only") is not True
                         or meta.get("fleet_auto_merge",False) is True):
        raise ValueError("Safe base advance only supports external PR-only tasks")
    if type(meta["fleet_ready"]) is not bool:
        raise ValueError("fleet_ready must be boolean")
    if execute and not meta["fleet_ready"]:
        raise ValueError("fleet_ready is false; missing acceptance prerequisites")
    return meta


def controller_command(key, meta, execute):
    model, profile, tool_profile, integrations = selected_agent_configuration(meta)
    cmd = [
        sys.executable, str(Path(__file__).with_name("oc_fleet_issue.py")), "run",
        "--repo", meta["fleet_target_repo"],
        "--issue", meta["fleet_target_issue"],
        "--worker-account", meta["fleet_worker_account"],
        "--worker-repo", meta["fleet_worker_repo"],
        "--target-account", meta["fleet_target_account"],
        "--model", model,
        "--profile", profile,
        "--agent", selected_agent_role(meta),
        "--multica-issue", key,
        "--require-approved-target",
    ]
    for scope in [meta["fleet_allowed_path"], *extra_allowed_paths(meta)]:
        cmd.extend(["--path",scope])
    if tool_profile:
        cmd.extend(["--tool-profile", tool_profile])
    for integration in integrations:
        cmd.extend(["--integration", integration])
    if meta.get("fleet_external_pr_only") is True:
        cmd.append("--external-pr-only")
    # Record this choice at first dispatch, not as new authorization at resume.
    if meta.get("fleet_allow_safe_base_advance") is True:
        cmd.append("--safe-base-advance-policy")
    # Only the resume path may append --allow-safe-base-advance, not dispatch.
    if not execute:
        cmd.append("--dry-run")
    return cmd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--mode", choices=("plan", "run"), default="plan")
    args = parser.parse_args()
    if not ISSUE_RE.fullmatch(args.issue) or not UUID_RE.fullmatch(args.project_id):
        raise ValueError("invalid issue or project selector")
    execute = args.mode == "run"
    meta = validate_issue(read_issue(args.issue), args.issue, args.project_id, execute)
    cmd = controller_command(args.issue, meta, execute)
    if not execute:
        print("MULTICA_HANDOFF_PLAN: no GitHub Action will be started.")
    result = subprocess.run(cmd, timeout=2000 if execute else 90, check=False)
    if result.returncode:
        raise ValueError("trusted controller rejected the routed task")
    if execute:
        print("MULTICA_HANDOFF_DISPATCHED: controller is source of truth for CI/PR.")
    else:
        print("MULTICA_HANDOFF_PLAN_VALIDATED")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError, json.JSONDecodeError) as exc:
        print("MULTICA_HANDOFF_BLOCKED: " + str(exc)[:260], file=sys.stderr)
        sys.exit(1)
