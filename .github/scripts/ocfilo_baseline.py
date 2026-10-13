#!/usr/bin/env python3
"""Read-only universal PR baseline; do not execute untrusted Python source.

Run from a trusted checkout of main against a separate candidate worktree.
"""
import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
SAFE_NAME = re.compile(r"[A-Za-z0-9_./+\- ]+\Z")


def git(root, *args):
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False
    )
    if process.returncode:
        raise ValueError(f"git {args[0]} failed ({process.returncode}): {process.stderr[-500:]}")
    return process.stdout


def validate(root, base, head="HEAD"):
    root = Path(root).resolve()
    if not (root / ".git").exists():
        raise ValueError("Candidate is not a checked-out repository")
    if not SHA.fullmatch(str(base)):
        raise ValueError("Base SHA must be a 40-digit git object")
    head_sha = git(root, "rev-parse", "--verify", head).strip()
    if not SHA.fullmatch(head_sha):
        raise ValueError("Invalid candidate SHA")
    # Revisions must be ancestors; refusal prevents diffing an unrelated base.
    git(root, "merge-base", "--is-ancestor", base, head_sha)
    git(root, "diff", "--check", base, head_sha, "--")
    # -z preserves spaces/unusual names and does not depend on localized quoting.
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--diff-filter=ACMR", "-z",
         base, head_sha, "--"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if result.returncode:
        raise ValueError("Could not list changed files")
    names = [v.decode("utf-8") for v in result.stdout.split(b"\0") if v]
    checked_py = []
    checked_sh = []
    for name in names:
        path = root / name
        if (not SAFE_NAME.fullmatch(name) or
            any(part in ("", ".", "..") for part in name.split("/")) or
            path.is_symlink() or not path.is_file()):
            raise ValueError("Invalid or non-regular changed source path")
        if name.endswith(".py"):
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=name)
            except (SyntaxError, UnicodeError) as e:
                raise ValueError(f"Python syntax failed: {name}: {e}") from e
            checked_py.append(name)
        elif name.endswith(".sh"):
            process = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            if process.returncode:
                raise ValueError(f"Shell syntax failed: {name}: {process.stderr[-400:]}")
            checked_sh.append(name)
    return {
        "outcome": "PASS",
        "base_sha": base, "head_sha": head_sha,
        "changed_files": len(names), "python_syntax_checked": checked_py,
        "shell_syntax_checked": checked_sh,
        "note": "Whitespace + changed Python/shell syntax only; not a full code or security review",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--head", default="HEAD")
    args = p.parse_args()
    print(json.dumps(validate(args.repo, args.base, args.head), indent=2))


if __name__ == "__main__":
    main()
