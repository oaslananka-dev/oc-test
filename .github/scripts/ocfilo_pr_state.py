#!/usr/bin/env python3
"""ocfilo F1 PR decision gate.

Read-only, deterministic decision. Does not call GitHub or launch an agent.
Input is a trusted GitHub API snapshot collected by the controller, not bot prose.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

REPO = "oaslananka-dev/oc-test"
BRANCH = re.compile(r"ocfilo/f1-[a-z0-9-]+\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED = frozenset({"fixture-quality", "semgrep-cloud-platform/scan"})
MAX_REPAIRS = 3
DONE = {"success"}
SKIPPED = {"neutral", "skipped"}
FAIL = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
KNOWN_BOTS = frozenset({
    "coderabbitai[bot]", "codacy[bot]", "dependabot[bot]",
    "github-actions[bot]", "semgrep-cloud-platform[bot]"
})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def decide(snapshot, ledger=None):
    """Return SHA-bound plan. Never mutate ledger or grant GitHub permissions."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    ledger = ledger or {}
    pr = snapshot.get("pr")
    if not isinstance(pr, dict):
        raise ValueError("missing PR")
    head = pr.get("head", {})
    base = pr.get("base", {})
    sha = head.get("sha")
    number = pr.get("number")
    if (
        type(number) is not int or number < 1
        or head.get("repo", {}).get("full_name") != REPO
        or base.get("repo", {}).get("full_name") != REPO
        or base.get("ref") != "main"
        or not BRANCH.fullmatch(str(head.get("ref", "")))
        or not SHA.fullmatch(str(sha))
    ):
        raise ValueError("PR is outside F1 authorization boundary")

    result = dict(repository=REPO, pr_number=number, head_sha=sha,
                  state="UNKNOWN", dispatch_allowed=False, repair_key=None,
                  failing_checks=[], pending_checks=[], missing_checks=[],
                  blocking_reviews=[], bot_feedback=[], reason="")
    if snapshot.get("event_sha") and snapshot["event_sha"] != sha:
        result.update(state="STALE_EVENT", reason="Event SHA is not current PR HEAD")
        return result
    if pr.get("state") != "open" or pr.get("merged") is True:
        result.update(state="CLOSED", reason="PR is closed or merged")
        return result

    checks_obj = snapshot.get("checks")
    if not isinstance(checks_obj, dict) or not isinstance(checks_obj.get("check_runs"), list):
        raise ValueError("missing check-run listing")
    runs = checks_obj["check_runs"]
    if checks_obj.get("total_count", len(runs)) != len(runs):
        raise ValueError("incomplete/paginated check-run listing")
    grouped = {}
    for check in runs:
        if not isinstance(check, dict):
            raise ValueError("invalid check-run")
        if check.get("head_sha") != sha:
            continue
        name = check.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("unnamed check-run")
        grouped.setdefault(name, []).append(check)
    required_missing = sorted(REQUIRED - grouped.keys())
    pending, failed = set(), set()
    for name, duplicates in grouped.items():
        # GitHub retains old check runs for the same commit/context. Prefer the
        # newest ID (monotonically increasing). If identities are missing or
        # ambiguous, fail closed by evaluating every candidate.
        ids = [check.get("id") for check in duplicates]
        if (len(duplicates) > 1 and all(type(ident) is int and ident > 0 for ident in ids)
                and len(set(ids)) == len(ids)):
            checks_to_evaluate = [max(duplicates, key=lambda check: check["id"])]
        else:
            checks_to_evaluate = duplicates
        for check in checks_to_evaluate:
            status = check.get("status")
            conclusion = check.get("conclusion")
            if status != "completed":
                pending.add(name)
            elif conclusion in FAIL:
                failed.add(name)
            elif conclusion not in (DONE | SKIPPED):
                # Unknown conclusions are blockers, not implicit success.
                failed.add(name)
            elif name in REQUIRED and conclusion not in DONE:
                failed.add(name)
    result.update(missing_checks=required_missing,
                  pending_checks=sorted(pending), failing_checks=sorted(failed))

    commit_status = snapshot.get("statuses")
    if not isinstance(commit_status, dict) or not isinstance(commit_status.get("statuses"), list):
        raise ValueError("missing commit status listing")
    status_contexts = {}
    for status in commit_status["statuses"]:
        if not isinstance(status, dict):
            raise ValueError("invalid commit status")
        if status.get("sha") not in (None, sha):
            continue
        label = "status:" + str(status.get("context", "?"))
        status_contexts.setdefault(label, []).append(status)
    for label, duplicates in status_contexts.items():
        if len(duplicates) > 1:
            # GitHub's combined-status API returns the latest context first.
            # Re-sort when timestamps are provided; if ordering is unknown,
            # fail closed rather than assuming the first result is authoritative.
            dated = [z.get("created_at") for z in duplicates]
            if all(isinstance(v, str) and v for v in dated) and len(set(dated)) == len(dated):
                duplicates = [max(duplicates, key=lambda z: z["created_at"])]
        for status in duplicates:
            if status.get("state") == "pending":
                pending.add(label)
            elif status.get("state") != "success":
                failed.add(label)
    result.update(pending_checks=sorted(pending), failing_checks=sorted(failed))

    if not isinstance(snapshot.get("reviews"), list):
        raise ValueError("missing reviews")
    latest = {}
    for review in snapshot["reviews"]:
        if not isinstance(review, dict):
            raise ValueError("invalid review")
        who = review.get("user", {}).get("login")
        if who and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[who] = review.get("state")
    result["blocking_reviews"] = sorted(name for name, state in latest.items()
                                         if state == "CHANGES_REQUESTED")

    for field in ("issue_comments", "review_comments"):
        if not isinstance(snapshot.get(field), list):
            raise ValueError(f"missing {field} listing")
        for comment in snapshot[field]:
            if not isinstance(comment, dict):
                raise ValueError("invalid comment")
            user = comment.get("user") or {}
            login = user.get("login", "")
            if user.get("type") == "Bot" or login.lower() in KNOWN_BOTS:
                # Never interpret bot comment body as an executable instruction.
                result["bot_feedback"].append(
                    {"id": comment.get("id"), "author": login, "source": field}
                )
    disposition = set(ledger.get("acknowledged_bot_comment_ids", []))
    unresolved = [item for item in result["bot_feedback"]
                  if item["id"] not in disposition]
    result["bot_feedback"] = unresolved

    if required_missing or pending:
        result.update(state="WAITING_CHECKS",
                      reason="Required checks missing or checks pending")
        return result

    if failed:
        signature = {"repo": REPO, "pr": number, "sha": sha, "checks": sorted(failed)}
        key = digest(signature)
        result["repair_key"] = key
        history = ledger.get("repairs", [])
        if not isinstance(history, list):
            raise ValueError("invalid repair ledger")
        if any(item.get("key") == key for item in history):
            result.update(state="WAITING_REPAIR",
                          reason="This check-failure fingerprint has already been dispatched")
        elif len(history) >= MAX_REPAIRS:
            result.update(state="NEEDS_HUMAN",
                          reason="Max repair attempts reached")
        else:
            result.update(state="REPAIR_REQUIRED", dispatch_allowed=True,
                          reason="A new SHA-bound repair task may be reviewed")
        return result

    if result["blocking_reviews"]:
        result.update(state="REVIEW_BLOCKED", reason="Changes requested by reviewer")
    elif unresolved:
        result.update(state="BOT_FEEDBACK_TRIAGE",
                      reason="Bot feedback must be evaluated and acknowledged")
    elif pr.get("draft") is True:
        result.update(state="READY_FOR_REVIEW", reason="Draft PR cannot be merge-ready")
    elif pr.get("mergeable") is not True or pr.get("mergeable_state") != "clean":
        result.update(state="MERGE_BLOCKED",
                      reason="GitHub has not verified PR as cleanly mergeable")
    elif pr.get("requested_reviewers") or pr.get("requested_teams"):
        result.update(state="REVIEW_PENDING",
                      reason="Requested reviews still pending")
    else:
        result.update(state="READY_FOR_MERGE",
                      reason="Current SHA F1 checks and visible PR conditions passed; never auto-merge")
    return result


def record_dispatch(ledger, decision, run_id):
    """Record dispatch ONLY after GitHub acknowledges a real workflow run."""
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Invalid GitHub Actions run ID")
    if decision.get("state") != "REPAIR_REQUIRED" or not decision.get("dispatch_allowed"):
        raise ValueError("Only a reviewed REPAIR_REQUIRED decision can be acknowledged")
    key = decision.get("repair_key")
    sha = decision.get("head_sha")
    if not isinstance(key, str) or not SHA.fullmatch(str(sha)):
        raise ValueError("Missing SHA-bound key")
    ledger = dict(ledger)
    history = list(ledger.get("repairs", []))
    if any(x.get("key") == key or x.get("run_id") == run_id for x in history):
        raise ValueError("Duplicate repair dispatch")
    if len(history) >= MAX_REPAIRS:
        raise ValueError("Repair limit exhausted")
    history.append({"key": key, "sha": sha, "run_id": run_id})
    ledger["repairs"] = history
    return ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--decision", required=True)
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8-sig")) if args.ledger else {}
    decision = decide(snapshot, ledger)
    Path(args.decision).write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
