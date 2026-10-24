"""Fail-closed read-only proof for an originally authorized safe base advance.

GitHub's source ancestry must be verified AND the exact Git objects for every
approved path must be equal in the original checkout and advanced parent.
No mutable branch references, downloaded content or trust in commit messages.
"""
import re

SHA = re.compile(r"[a-f0-9]{40}\Z")
MAX_PATHS = 12


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verified_sha(value):
    return type(value) is str and bool(SHA.fullmatch(value))


def tree_root(repo, commit_sha, api):
    require(verified_sha(commit_sha), "Unsafe source commit")
    doc = api(f"repos/{repo}/git/commits/{commit_sha}")
    tree = doc.get("tree") if type(doc) is dict else None
    require(type(tree) is dict and verified_sha(tree.get("sha"))
            and doc.get("sha") == commit_sha, "Missing signed-off Git tree identity")
    return tree["sha"]


def path_object(repo, root, path, api):
    """Get exact Git tree/blob object for a scoped path, or None if absent."""
    require(type(path) is str and 0 < len(path) <= 4096, "Invalid scope")
    want_dir = path.endswith("/")
    parts = path.rstrip("/").split("/")
    require(all(part not in ("", ".", "..") for part in parts)
            and not any("\\" in part or "\x00" in part for part in parts),
            "Unsafe scope components")
    tree_sha = root
    for i, part in enumerate(parts):
        doc = api(f"repos/{repo}/git/trees/{tree_sha}")
        entries = doc.get("tree") if type(doc) is dict else None
        require(type(entries) is list and len(entries) < 1000
                and doc.get("truncated") is False
                and doc.get("sha") == tree_sha, "Incomplete Git tree")
        found = [x for x in entries if type(x) is dict and x.get("path") == part]
        require(len(found) <= 1, "Ambiguous Git tree path")
        if not found:
            return None
        entry = found[0]
        sha = entry.get("sha")
        mode = entry.get("mode")
        kind = entry.get("type")
        require(verified_sha(sha)
                and kind in ("blob", "tree")
                and type(mode) is str, "Unsafe Git tree entry")
        final = i == len(parts)-1
        if final:
            require(kind == ("tree" if want_dir else "blob"),
                    "Approved path type changed")
            return (kind, mode, sha)
        require(kind == "tree" and mode == "040000",
                "Traversal crossed non-directory")
        tree_sha = sha
    raise ValueError("Invalid scope traversal")


def validate(repo, original_sha, new_parent_sha, paths, api):
    require(type(repo) is str and "/" in repo
            and verified_sha(original_sha) and verified_sha(new_parent_sha)
            and original_sha != new_parent_sha
            and type(paths) is list and 1 <= len(paths) <= MAX_PATHS,
            "Unapproved safe-base-advance proof")
    diff = api(f"repos/{repo}/compare/{original_sha}...{new_parent_sha}")
    require(type(diff) is dict
            and diff.get("status") == "ahead"
            and diff.get("behind_by") == 0
            and type(diff.get("ahead_by")) is int
            and 1 <= diff["ahead_by"] <= 250
            and type(diff.get("total_commits")) is int
            and diff["total_commits"] == diff["ahead_by"]
            and (diff.get("base_commit") or {}).get("sha") == original_sha
            and (diff.get("merge_base_commit") or {}).get("sha") == original_sha,
            "Advanced parent is not a bounded descendant of original source")
    old_root = tree_root(repo, original_sha, api)
    new_root = tree_root(repo, new_parent_sha, api)
    for path in paths:
        old_object = path_object(repo, old_root, path, api)
        new_object = path_object(repo, new_root, path, api)
        require(old_object == new_object,
                "Advanced parent changed an approved path")
    return True
