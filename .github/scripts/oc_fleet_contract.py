#!/usr/bin/env python3
"""Unprivileged, fail-closed task contract and patch evidence for portable fleet workers."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ALLOWED_KEYS = frozenset({
    "schema_version", "task_id", "source", "repository", "checkout_sha",
    "base_branch", "issue_key", "prompt", "model", "profile", "allowed_paths",
    "phase", "publish", "integrations", "tool_profile",
})
REQUIRED_KEYS = ALLOWED_KEYS - {"integrations", "tool_profile"}
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,47}\Z")
ISSUE_RE = re.compile(r"[A-Z][A-Z0-9]{1,14}-[1-9][0-9]{0,9}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,90}\Z")
MODELS = frozenset({"opencode/big-pickle", "opencode/ling-3.1-flash-free", "opencode/nemotron-3-ultra-free"})
PROFILES = frozenset({"base", "python-ruff", "security-semgrep"})
FORBIDDEN_PARTS = frozenset({".git", ".github", ".opencode", ".env", "node_modules", "__pycache__"})
MAX_PATCH_BYTES = 100_000


def clean_relpath(value, directory=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 180 or "\\" in value or "\x00" in value:
        return False
    if directory != value.endswith("/"):
        return False
    parts = value[:-1].split("/") if directory else value.split("/")
    return bool(parts) and all(
        part not in ("", ".", "..") and not part.startswith(".")
        and part not in FORBIDDEN_PARTS
        and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
        for part in parts
    )


def parse_manifest(raw, allowed_repositories):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 20_000:
        raise ValueError("manifest too large")
    obj = json.loads(raw)
    if not isinstance(obj, dict) or not REQUIRED_KEYS <= set(obj) or not set(obj) <= ALLOWED_KEYS:
        raise ValueError("missing or unapproved manifest fields")
    if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        raise ValueError("unsupported schema version")
    if not isinstance(obj["task_id"], str) or not ID_RE.fullmatch(obj["task_id"]):
        raise ValueError("invalid task ID")
    if obj["source"] not in ("frontier", "rules"):
        raise ValueError("invalid source")
    repo = obj["repository"]
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise ValueError("invalid repository")
    if repo.lower() not in {v.lower() for v in allowed_repositories}:
        raise ValueError("target repository not authorized by trusted allowlist")
    if not isinstance(obj["checkout_sha"], str) or not SHA_RE.fullmatch(obj["checkout_sha"]):
        raise ValueError("checkout SHA must be immutable")
    branch = obj["base_branch"]
    if (not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch) or ".." in branch
            or "//" in branch or branch.endswith(("/", ".")) or "/." in branch):
        raise ValueError("invalid base branch")
    if not isinstance(obj["issue_key"], str) or not ISSUE_RE.fullmatch(obj["issue_key"]):
        raise ValueError("invalid issue key")
    prompt = obj["prompt"]
    if not isinstance(prompt, str) or not 10 <= len(prompt) <= 8_000 or "\x00" in prompt:
        raise ValueError("invalid prompt")
    if (not isinstance(obj["model"], str) or not isinstance(obj["profile"], str)
            or obj["model"] not in MODELS or obj["profile"] not in PROFILES):
        raise ValueError("unapproved model or profile")
    integrations = obj.get("integrations", [])
    if (not isinstance(integrations, list)
            or len(integrations) > 3
            or len(set(str(x) for x in integrations)) != len(integrations)
            or any(not isinstance(x, str) or x not in ("context7", "codacy", "sonarqube")
                   for x in integrations)):
        raise ValueError("unknown or duplicate curated integration")
    tool_profile = obj.get("tool_profile", "")
    if (not isinstance(tool_profile, str)
            or (tool_profile and not re.fullmatch(r"[a-z][a-z0-9-]{0,47}",tool_profile))):
        raise ValueError("invalid selected tool profile")
    if obj["phase"] != "implement":
        raise ValueError("repair stage is not yet authorized")
    if obj["publish"] is not False:
        raise ValueError("this worker does not publish or merge")
    paths = obj["allowed_paths"]
    if not isinstance(paths, list) or not 1 <= len(paths) <= 12:
        raise ValueError("allowed paths must contain 1 to 12 entries")
    if not all(clean_relpath(p, directory=p.endswith("/")) for p in paths if isinstance(p, str)) or any(not isinstance(p,str) for p in paths):
        raise ValueError("invalid allowed path")
    if len({p.casefold() for p in paths}) != len(paths):
        raise ValueError("duplicate/case-colliding allowed paths")
    return obj


def path_allowed(path, scopes):
    if not clean_relpath(path):
        return False
    return any(path.startswith(p) if p.endswith("/") else path == p for p in scopes)


def git(repo, *args, binary=False):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=not binary)


def verify_checkout(repo, sha):
    if git(repo, "rev-parse", "HEAD").strip() != sha:
        raise ValueError("checkout HEAD is not the approved SHA")
    if git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        return False
    return True


def scoped_patch(repo, scopes):
    repo = Path(repo)
    subprocess.run(["git", "-C", str(repo), "add", "-N", "-A"], check=True)
    paths = [v.decode("utf-8") for v in git(repo, "diff", "--no-renames", "--name-only", "-z", "HEAD", binary=True).split(b"\0") if v]
    if not paths or len(paths) > 40 or len({p.casefold() for p in paths}) != len(paths):
        raise ValueError("no change, too many files, or case-colliding paths")
    for path in paths:
        target = repo / path
        if not path_allowed(path, scopes):
            raise ValueError("file outside approved scope: " + repr(path[:120]))
        current = repo
        for part in path.split("/"):
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("symlink in changed path")
        if not target.is_file() or not stat.S_ISREG(target.lstat().st_mode):
            raise ValueError("deleted or non-regular file")
    subprocess.run(["git", "-C", str(repo), "diff", "--no-renames", "--check", "HEAD"], check=True)
    patch = git(repo, "diff", "--no-ext-diff", "--no-renames", "--binary", "HEAD", binary=True)
    if not patch or len(patch) > MAX_PATCH_BYTES or b"GIT binary patch" in patch or b"Binary files " in patch:
        raise ValueError("empty/oversized/binary patch")
    patch.decode("utf-8")
    return paths, patch


def create_evidence(manifest, repo, output, run_id, attempt):
    if git(repo, "rev-parse", "HEAD").strip() != manifest["checkout_sha"]:
        raise ValueError("agent base changed")
    paths, patch = scoped_patch(repo, manifest["allowed_paths"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "changes.patch").write_bytes(patch)
    result = {
        "schema_version": 1, "task_id": manifest["task_id"], "source": manifest["source"],
        "repository": manifest["repository"], "checkout_sha": manifest["checkout_sha"],
        "issue_key": manifest["issue_key"], "run_id": str(run_id),
        "run_attempt": str(attempt), "outcome": "SUCCEEDED", "paths": paths,
        "patch_sha256": hashlib.sha256(patch).hexdigest(), "published": False,
    }
    (output / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return result


def create_failure_evidence(manifest, output, run_id, attempt):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1, "task_id": manifest["task_id"], "source": manifest["source"],
        "repository": manifest["repository"], "checkout_sha": manifest["checkout_sha"],
        "issue_key": manifest["issue_key"], "run_id": str(run_id),
        "run_attempt": str(attempt), "outcome": "FAILED", "paths": [],
        "patch_sha256": None, "published": False,
    }
    (output / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return result


def cli():
    if len(sys.argv) != 2 or sys.argv[1] not in ("validate", "gate"):
        raise ValueError("usage: oc_fleet_contract.py validate|gate")
    raw_allow = os.getenv("FLEET_ALLOWED_REPOS", "").strip() or "oaslananka-dev/oc-test"
    allow = [s.strip() for s in raw_allow.split(",") if s.strip()]
    manifest = parse_manifest(os.environ["FLEET_MANIFEST_JSON"], allow)
    if sys.argv[1] == "validate":
        out = Path(os.environ["GITHUB_OUTPUT"])
        with out.open("a", encoding="utf-8") as f:
            for key, value in (("target", manifest["repository"]), ("sha", manifest["checkout_sha"]),
                               ("model", manifest["model"]), ("profile", manifest["profile"]),
                               ("task_id", manifest["task_id"])):
                f.write(f"{key}={value}\n")
        print("Fleet contract validated", manifest["task_id"], manifest["source"])
    else:
        if os.environ.get("FLEET_AGENT_OK") != "true":
            create_failure_evidence(manifest, os.environ["FLEET_RESULT"],
                                    os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
            raise ValueError("agent did not succeed; failure evidence recorded")
        try:
            result = create_evidence(manifest, os.environ["FLEET_TARGET"], os.environ["FLEET_RESULT"],
                                     os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
        except (ValueError, TypeError, OSError, subprocess.CalledProcessError, UnicodeError) as error:
            # Fail closed, but always preserve bounded, non-publishing evidence.
            create_failure_evidence(manifest, os.environ["FLEET_RESULT"],
                                    os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
            raise ValueError("scope gate: " + type(error).__name__ + ": " + str(error)[:160] + "; failure evidence recorded") from error
        print("Fleet evidence created for", len(result["paths"]), "file(s)")


if __name__ == "__main__":
    try:
        cli()
    except (ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError, UnicodeError) as e:
        raise SystemExit(f"fleet rejected: {type(e).__name__}: {str(e)[:200]}")
