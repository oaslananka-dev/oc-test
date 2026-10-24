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

This first version refuses safe-base-advance PRs and protection policies
with no independently readable required checks. A former human merge may
have bypassed protection: GitHub's `merged=true` flag alone is never proof
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

No active Fovux/ADXL355 PR was merged or marked done to validate this
feature. Its release acceptance requires full offline regression and
a reviewable, green controller PR; real successful terminal readback
remains pending a separately approved qualifying external merge.
