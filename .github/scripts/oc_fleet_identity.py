"""Verified, human GitHub identities for commits created by automation.

Automation is an execution mechanism, never a Git author or co-author.
"""
import re
import subprocess

HANDLE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
AUTOMATION = re.compile(r"(?i)\b(?:ocfilo|oc-test|opencode|fleet[-_ ]?controller|integrator[-_ ]?bot|github-actions(?:\[bot\])?)\b")
TRAILER = re.compile(r"(?im)^(?:co-authored-by|signed-off-by)\s*:")


def github_user_identity(user):
    if not isinstance(user, dict) or user.get("type") != "User":
        raise ValueError("publisher must authenticate as a GitHub user, not an App/bot")
    login = user.get("login")
    user_id = user.get("id")
    if not isinstance(login, str) or not HANDLE.fullmatch(login):
        raise ValueError("selected GitHub login invalid")
    if type(user_id) is not int or user_id < 1:
        raise ValueError("selected GitHub numeric ID missing")
    display = user.get("name")
    name = display.strip() if isinstance(display, str) and display.strip() else login
    if len(name) > 100 or "\n" in name or "\r" in name or "<" in name or ">" in name:
        raise ValueError("unsafe GitHub display name")
    if AUTOMATION.search(name) or AUTOMATION.search(login):
        raise ValueError("machine identity cannot author repository commits")
    return name, f"{user_id}+{login}@users.noreply.github.com"


def validate_local_identity(name, email):
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("explicit Git author name required")
    if not isinstance(email, str) or not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[A-Za-z]{2,}", email):
        raise ValueError("explicit Git author email required")
    if AUTOMATION.search(name) or AUTOMATION.search(email):
        raise ValueError("automation identity is forbidden")


def verify_commit(repo, name, email):
    validate_local_identity(name, email)
    log = subprocess.check_output([
        "git", "-C", str(repo), "log", "-1",
        "--format=%an%x00%ae%x00%cn%x00%ce%x00%B"
    ], text=True, encoding="utf-8")
    fields = log.split("\x00", 4)
    if len(fields) != 5 or fields[:4] != [name, email, name, email]:
        raise ValueError("commit author/committer differ from the selected GitHub identity")
    message = fields[4]
    if TRAILER.search(message) or AUTOMATION.search(message):
        raise ValueError("commit contains an unauthorized trailer or automation metadata")
    return True
