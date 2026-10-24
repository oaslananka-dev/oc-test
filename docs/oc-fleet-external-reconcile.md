# Manual external PR terminal reconciliation

`oc_fleet_external_finalize.py` is a **separate operator-invoked,
default-read-only** completion path for an existing external GitHub OC Fleet
task. It does **not** alter the unattended four-slot dispatcher, auto-finalize
pilot, worker model or allowed scopes. No new service, token or scheduler.

This is **not** an automatic merge mechanism. A completed GitHub worker and
an opened PR mean `in_review`, not `done`.

## Preconditions and read-only check

From the trusted controller checkout, with the existing GitHub/Multica CLI
access and the **original local dispatch receipt**:

```sh
python3 -B .github/scripts/oc_fleet_external_finalize.py \
  --issue OAS-39 \
  --project-id 9d64209f-2ba7-441b-a204-d5fd7bf0f962
```

At the time of the first live acceptance, Fovux PR #278 was **still open with
required security failures**. Expected default response: `waiting`,
`pr_not_merged`. Nothing is dispatched, merged or written to Multica.

A `ready` response is only an evidence report, **not approval**. The
operator must review the exact task, source commit, PR, branch rules and
merge SHA. The action uses read-only GitHub REST APIs:

- Original Multica issue/project and immutable local receipt match the
  external PR-only task, original worker run, role/model/profile and
  exact allowed paths; unsafe, stale or incomplete evidence fails closed
- GitHub confirms the original workflow-dispatch worker run was successful
- The same-repo PR is merged into `main`, closes the expected issue,
  contains one original-head commit parented by the receipt checkout SHA,
  and changes *only* approved files
- Current effective GitHub **branch rules** identify required CI/security
  checks; actual successful check runs and commit statuses at the PR head
  must be complete, with **no failed check anywhere**, and the issue
  must be closed

Original-receipt-authorized `fleet_allow_safe_base_advance=true` is also
supported, **only** when the PR's one parent is a bounded descendant of the
original checkout SHA and the Git **blob/tree identity of each approved path**
is unchanged between old and advanced baselines. Use immutable Git commit
objects and compare/ancestry evidence; never accept PR body claims or merely
a changed base SHA. The default without opt-in remains an exact-parent match.
Policies with no independently readable required checks still fail closed.
A former human merge may have bypassed protection: GitHub's `merged=true` flag alone is never proof
of security acceptance. PRs with more than 99 check runs/files or
ambiguous evidence are deliberately blocked.

## Explicit post-review state update

Only after *separate operator approval for the particular live task*, and
only when a fresh dry run says `ready`, run with both exact confirmations:

```sh
python3 -B .github/scripts/oc_fleet_external_finalize.py \
  --issue ISSUE-KEY \
  --project-id PINNED-PROJECT-UUID \
  --apply --confirm-issue ISSUE-KEY \
  --confirm-merge-sha VERIFIED-40-CHAR-MERGE-SHA
```

The script rechecks authoritative evidence, writes only `fleet_merge_sha`,
`fleet_result=completed`, and Multica `done`, and re-reads the live
Multica issue before claiming `applied`. No action is triggered when
evidence is missing, ambiguous or pending. The existing automatic
pilot finalizer remains unchanged.

If the CLI fails after any of the three sequential metadata/status writes,
**do not retry blindly**: retain the receipt and inspect the partial
Multica state before separately reconciling. Already-complete state
with the matching merge SHA is read-only idempotent; a mismatched
completion or partial transition fails closed.

The real external post-merge terminal path was accepted on Multica OAS-31 /
ADXL355 PR #145: independently protected merge #65d6a46, 18 required
checks, `fleet_result=completed`, actual `done` and repeat read-only
`already_completed` evidence. This **does not** complete OAS-39: its original
Fovux worker PR #278 was closed **unmerged** after a distinct and broader
security PR #281 was independently merged.

## Superseded external worker PR: deliberately NOT success

`oc_fleet_external_supersede.py` is a separate **operator-only, default
read-only** path. It handles an existing externally delegated task whose
original authenticated worker PR was explicitly closed *unmerged* because a
different, independently accepted PR superseded it. It never calls a worker,
starts a Multica run, merges a PR, marks the original run `completed`, or
rewrites its original PR number / run ID / merge SHA.

It verifies the immutable original worker receipt, exact SHA and allowed
paths, actual successful worker run, original PR identity and **closed,
unmerged** state, and original PR path inventory. Then it verifies the
operator-nominated **distinct** replacement PR actually merged into `main`,
its merge commit is a bounded ancestor of present protected `main`, that the
replacement touches every file in the original PR, that the source issue is
`closed / completed`, and that **all current effective GitHub-required
security and CI checks on the replacement head passed**. Unsafe, incomplete
or conflicting evidence fails closed. These are necessary conditions, **not
proof that the replacement semantically resolves the original task**.

Default read-only check for the known Fovux case:

```sh
python3 -B .github/scripts/oc_fleet_external_supersede.py \
  --issue OAS-39 \
  --project-id 9d64209f-2ba7-441b-a204-d5fd7bf0f962 \
  --replacement-pr 281 \
  --replacement-merge-sha 43204aed9db9c17e20fa6373b5d42a2c6e9c8cfb
```

`ready_to_cancel` is **not** permission to cancel the task. Separately,
the responsible operator must explicitly review semantic equivalence,
the whole original and replacement diffs, and confirm exactly which
issue, replacement PR and merge SHA to record. **Only after that review
and authorization**, the intentionally non-automatic command is:

```sh
python3 -B .github/scripts/oc_fleet_external_supersede.py \
  --issue ISSUE-KEY \
  --project-id PROJECT-UUID \
  --replacement-pr VERIFIED-REPLACEMENT-PR \
  --replacement-merge-sha VERIFIED-40-CHAR-SHA \
  --apply --confirm-issue ISSUE-KEY \
  --confirm-replacement-pr VERIFIED-REPLACEMENT-PR \
  --confirm-merge-sha VERIFIED-40-CHAR-SHA \
  --confirm-resolution-reviewed
```

The explicit operation writes **only** `fleet_superseded_by_pr`,
`fleet_superseded_merge_sha`, `fleet_result=superseded` and Multica
`status=cancelled --no-start`, then reads back exact state. The original
`fleet_pull_request`, `fleet_run_id` and `fleet_merge_sha=null` remain
unchanged. Repeat read-only execution returns `already_superseded`;
a partial result, wrong alternate commit, missing source receipt or
a preexisting `done` fails closed. On any partial write failure,
**do not blindly rerun**: inspect the actual task record first.

Keep the success-only `oc_fleet_external_finalize.py` contract unchanged.
In particular, a superseded PR must **never** count as a successful
external worker merge/`done` acceptance. This separate lifecycle
feature is not added to any systemd timer, dispatch loop or autopilot.
