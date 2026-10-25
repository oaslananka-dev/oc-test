#!/usr/bin/env python3
"""Explicitly registered multi-account GitHub Actions worker selection.

Only Doppler FLEET_WORKER_REPOS members are eligible; the repo owner is the
worker GitHub account. This module neither creates accounts nor grants access.
"""
import hashlib
import os
import re
import subprocess

import oc_fleet_issue as worker

DEFAULT_REPO = "MakerParsDev/oc-test"
DEFAULT_ACCOUNT = "MakerParsDev"
MAX_POOL = 10
GLOBAL_MAX = 20
PER_ACCOUNT_DEFAULT = 4
WORKFLOW = "oc-fleet-worker.yml"
ACTIVE_STATES = ("queued", "in_progress", "waiting", "requested", "pending")


def require(condition, message):
    if not condition:
        raise ValueError(message)



def configured_capacity():
    """Bounded operator configuration, never inferred from number of GH accounts alone."""
    def count(name, default, highest):
        raw = os.environ.get(name, str(default))
        require(isinstance(raw,str) and re.fullmatch(r"[1-9][0-9]{0,2}",raw),
                f"{name} must be a positive integer")
        value = int(raw)
        require(1 <= value <= highest, f"{name} outside approved range")
        return value
    return count("FLEET_PER_ACCOUNT_WORKERS", PER_ACCOUNT_DEFAULT, 8), count(
        "FLEET_GLOBAL_WORKERS", GLOBAL_MAX, GLOBAL_MAX)

def registered():
    env = os.environ.copy()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "DOPPLER_TOKEN"):
        env.pop(name, None)
    process = subprocess.run(
        ["doppler", "secrets", "get", "FLEET_WORKER_REPOS", "--plain",
         "--project", "oc-test", "--config", "main"],
        env=env, text=True, capture_output=True, timeout=25,
    )
    require(process.returncode == 0, "Trusted worker pool registry unavailable")
    names = [x.strip() for x in process.stdout.strip().split(",")]
    require(1 <= len(names) <= MAX_POOL
            and all(re.fullmatch(worker.REPO, x) for x in names)
            and len({x.casefold() for x in names}) == len(names),
            "Malformed or duplicate worker registration")
    require(DEFAULT_REPO.casefold() in {x.casefold() for x in names},
            "Default trusted worker was removed from registration")
    # Keep deterministic account/repo pairing; org-owned repos require a
    # separate explicitly approved account mapping in a future reviewed change.
    return sorted(names, key=lambda n: (n.casefold() != DEFAULT_REPO.casefold(),
                                        n.casefold()))


def probe(repo):
    """Prove actor, approved workflow and exact live run count for one repo."""
    require(type(repo) is str and re.fullmatch(worker.REPO, repo),
            "Invalid registered worker repository")
    account = repo.split("/", 1)[0]
    identity = worker.identity(account)  # validates gh login with this account
    verified = worker.authorized_worker_repo(identity, repo)
    require(verified.casefold() == repo.casefold(),
            "Worker identity/repository mismatch")
    configured = worker.gh(identity,
        f"repos/{repo}/actions/variables/FLEET_WORKER_REPOSITORY")
    require(configured.get("name") == "FLEET_WORKER_REPOSITORY"
            and configured.get("value") == verified,
            "Worker's trusted Actions repository variable is missing or wrong")
    busy = 0
    for status in ACTIVE_STATES:
        response = worker.gh(identity,
            f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?"
            f"status={status}&per_page=100")
        runs = response.get("workflow_runs")
        count = response.get("total_count")
        require(type(count) is int and isinstance(runs, list)
                and count == len(runs) and len(runs) < 100
                and all(isinstance(r, dict) for r in runs),
                "Incomplete worker GitHub Actions capacity evidence")
        busy += count
    return {"repo":verified, "account":account, "busy":busy}


def snapshot():
    rows = [probe(repo) for repo in registered()]
    per_account, global_ceiling = configured_capacity()
    limit = min(global_ceiling, len(rows) * per_account)
    total = sum(row["busy"] for row in rows)
    for row in rows:
        row["limit"] = per_account
        row["free"] = max(0, per_account - row["busy"])
    return {"workers": rows, "total_active": total,
            "global_limit": limit, "per_account_limit": per_account,
            "global_free": max(0, limit - total)}


def choose(repo, issue, *, required_worker=None):
    """Choose an approved runner once; its identity becomes immutable metadata."""
    require(type(repo) is str and re.fullmatch(worker.REPO, repo)
            and type(issue) is int and issue > 0,
            "Invalid target task")
    state = snapshot()
    pool = state["workers"]
    if required_worker is not None:
        pool = [w for w in pool if w["repo"].casefold() == required_worker.casefold()]
        require(len(pool) == 1, "Previous worker no longer approved or available")
    else:
        pool=[w for w in pool if w["free"] > 0]
        require(pool, "No registered worker has available capacity")
        # Prefer less-busy accounts; fair tie-break for different GitHub issues.
        rotate = int.from_bytes(hashlib.sha256(
            (repo.casefold() + "#" + str(issue)).encode()).digest()[:4], "big") % len(pool)
        pool = [min(enumerate(pool),
                    key=lambda pair: (pair[1]["busy"],
                                      (pair[0] - rotate) % len(pool)))[1]]
    selected = pool[0]
    return {**selected,
            "global_active": state["total_active"],
            "global_free": state["global_free"],
            "global_limit": state["global_limit"]}


def global_slots():
    """Shared dynamic budget across registered worker accounts."""
    return snapshot()["global_free"]
