# Model-free Multica issue handoff

This adapter accepts an explicitly prepared Multica issue as a routing request
for the trusted GitHub Actions worker controller. It **does not** invoke a Multica
native agent or create an Autopilot. GitHub Actions stays responsible for the
worker's terminal status, immutable patch artifact, PR/CI, and final delivery.

The trusted operator runs:

```bash
python3 .github/scripts/oc_fleet_multica.py \
  --issue OAS-14 \
  --project-id 0506e0e5-01a7-4de9-b63b-67e993809721 \
  --mode plan
```

This checks the Multica issue and its exact project binding, reads the routing
metadata, and calls the existing controller in `--dry-run` mode. It creates no
GitHub Actions run. The selected Multica issue must be in a dispatchable status,
must not be assigned to a native agent, and must contain the exact fields:

- `fleet_target_repo`, `fleet_target_issue`
- `fleet_worker_repo`, `fleet_worker_account`, `fleet_target_account`
- `fleet_allowed_path`, `fleet_ready` (boolean)

The small pilot is deliberately limited to one file in `playground/fleet/`.
The existing controller additionally enforces GitHub permissions, Doppler
allowlists, exact source-run identity, retries and scoped artifacts. A successful
plan is *not* an authorization to publish or merge.

A real execution is `--mode run`, and it is rejected unless
`fleet_ready=true`, set only after the external worker's read-only Doppler
bootstrap, source workflow, and GitHub identities are verified. The operator
must first confirm that a previous receipt does not represent a live or
already-successful action. Do not retry an uncertain dispatch.

## Status (2026-10-20)

- `MakerParsDev/oc-test` is public and carries the portable worker workflow.
- Its Actions `FLEET_WORKER_REPOSITORY` variable is set.
- `FLEET_WORKER_REPOS` in Doppler includes that worker.
- A GitHub Actions **read-only Doppler bootstrap secret** is still missing.
- OAS-14 is `backlog` and `fleet_ready=false`; no worker was dispatched.

There is **no automatic Multica issue event trigger** in this version. A future
trusted operator or scheduled job can select prepared issues; it must not use
free-form issue text as execution authority. Do not enable Multica native
OpenCode V2 execution as a workaround for the known terminal-event bug.

## Optional autonomous single-operator polling

The approved Multica project can be polled using
`.github/scripts/oc_fleet_multica_poll.py`. By default it plans the first
`fleet_ready=true` issue without launching a worker. The `--run` switch
executes **at most one issue**, under the existing controller's repository,
credential, workflow and patch verification gates. A local atomic claim,
controller receipt and systemd flock guard make uncertain results fail closed.
Claims/receipts are not silently cleared or retried.

The reviewed user-level service and five-minute timer live in `ops/systemd/`.
Deploy them on **one** authorized operator host only, with the checkout fixed
at `~/.local/share/oc-fleet-dispatch`. Never give the GitHub-hosted worker
the operator's write credentials.

When GitHub PR publication succeeds, the poller marks the Multica issue
`in_review` and turns `fleet_ready` off. CI review and merge remain under
the normal protected GitHub workflow; the poller does not merge automatically.
If dispatch fails after creating a claim, stop and reconcile the worker
receipt/run; do not remove claims as a routine retry strategy.

## Explicit CI/review completion (pilot repository only)

For test fixture issues in `oaslananka-dev/oc-test`, an authorized Multica
issue may specify `fleet_auto_merge=true` (boolean). This never applies to
other repositories or production releases. After controller PR publication,
the poller saves `fleet_pull_request` alongside its trusted run receipt.

A later poll completes at most one eligible `in_review` issue when:

- It belongs to the pinned Multica project and has the explicit opt-in.
- Its immutable controller receipt matches worker, run, GitHub issue and scope.
- The exact PR changes only the single approved file and closes that issue.
- The PR has a clean merge state, required `baseline` and Semgrep results are
  **SUCCESS**, other checks have completed, and no review discussions remain.
- Merge uses GitHub's protected squash-merge with exact head-SHA matching.
- GitHub reports the PR merged, the target issue closed and the file present on
  `main` before Multica is marked `done`.

If GitHub merges but the local process stops before Multica is updated, the
next poll reconciles the already-merged PR without merging it a second time.
No review-comment autofix, privileged branch-policy bypass, release/deployment,
or general-purpose cross-repository automerge is enabled.
