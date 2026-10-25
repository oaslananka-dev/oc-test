# Multi-account worker pool (task-driven, free GitHub Actions)

The Fleet remains **task-driven**. It does not discover, create or run new work
without an operator-assigned GitHub issue. A local control agent or ChatGPT may
submit that authorized issue; AMD2700X's existing timer dispatches only
`fleet_ready=true` Multica tasks.

## Why this pool

`oc-fleet plan` and `oc-fleet submit` used to pin
`MakerParsDev/oc-test`. They now inspect only public worker repositories
registered in the existing Doppler secret `FLEET_WORKER_REPOS` under
`oc-test/main`. Each repository's **owner** is the exact GH CLI worker
account; implicit cross-account impersonation and organization-owned runners
are not accepted by this version. The default worker must remain present.

The trusted selector validates for **every registered worker**:

- its exact `OWNER/REPO` identity (no duplicates or shell syntax)
- a locally authenticated GitHub account with repository push permission
- a public, unarchived worker repository
- active reviewed `.github/workflows/oc-fleet-worker.yml` at the expected path
- live complete GitHub Actions counts in five active run states
- allowlisting via existing `FLEET_WORKER_REPOS`, enforced again during dispatch

If a registered account cannot be authenticated or counted, capacity is
**unknown** and no task is admitted. Configure or remove the account through
the authorized registry; do not bypass that evidence or switch GH's global
active identity. No worker receives the privileged *target* publish token.

## Selection and capacity

`oc-fleet workers` and MCP `fleet_worker_pool` show account, worker
repository, real active run count, and shared capacity; no tokens.

`oc-fleet plan --repo OWNER/REPO --issue 123 --path src/a.py` returns
the selected worker repo and account. Selection prefers the **least-busy**
registered worker, then uses a deterministic hash of the target issue for
fair tie distribution. `submit` repeats the selection under the existing
dispatcher lock; the exact worker repo/account are written into immutable
Multica routing metadata *before* `fleet_ready=true`. One task is not
dispatched to multiple workers. A failed original task retried using
`fleet_retry_failed` keeps the same approved worker account and fresh
independently verified attempt-1 run, preserving original provenance.

**Total concurrency is dynamically limited by registered account capacity.**
By default each verified worker account admits up to four concurrent runs;
the shared ceiling is `min(20, registered_accounts * 4)` (two accounts: eight).
The operator may override the bounded limits through
`FLEET_PER_ACCOUNT_WORKERS` (1..8) and `FLEET_GLOBAL_WORKERS` (1..20)
in the controller service environment. The total is still bounded by both,
never automatically raised to a provider's undocumented entitlement.
A task must also pass repository, file-scope and active-run preflight.

The controller now reads all registered worker run counts and applies the
shared global ceiling in addition to per-repository checks, including runs
started on earlier timer ticks. The Fovux service and ordinary Fleet use
the existing dispatcher lock. Unknown capacity fails closed. A full pool queues work rather than exceeding its configured budget. Actual free-model
provider quotas are external and are not assumed to equal Actions quotas.

## Adding an account (deliberate operator action)

1. Create/select a public dedicated worker repository under the *approved*
   GitHub user account. Do not grant the worker target-repo publication
   privileges. Copy the reviewed original worker workflow and matching
   scripts at the same trusted protected revision.
2. Set GitHub repository Actions variable
   `FLEET_WORKER_REPOSITORY=owner/repo`, and configure the authorized
   read-only `DOPPLER_TOKEN` bootstrap for `oc-test/main`. Keep credentials
   entirely outside issue prompts and model/tool traces.
3. Authenticate that exact worker GitHub account on AMD2700X through the
   operator's GitHub CLI. Confirm that the token resolves to its expected
   account and has push/Actions trigger access to its own dedicated runner.
4. **After** the worker's real GH permissions, bootstrap and workflow are
   verified, append its exact `owner/repo` to the existing
   `FLEET_WORKER_REPOS` in Doppler. Do not auto-authorize account names or
   repositories from task text. Default `MakerParsDev/oc-test` stays present.
5. Run `oc-fleet workers`; then make one **read-only** `oc-fleet plan`
   and perform an isolated manual-merge-only test on that new account. Verify
   GH Actions run, immutable dispatch receipt, scoped PR, CI/security and
   Multica status before accepting the account into live task routing.

A local agent can instead call `fleet_worker_pool`, `fleet_plan`,
`fleet_submit`, `fleet_status`, `fleet_pr`, `fleet_checks` and the
existing exact-SHA reviewed protected merge/finalization tools. It cannot
approve new GitHub accounts through a model prompt.

## Current acceptance boundary

The currently verified live registration is **only**
`MakerParsDev/oc-test`; more authenticated GH accounts alone do not
constitute worker setup. Automated account balancing has deterministic
offline two-account tests, real default-account preflight, and the same
fail-closed production admission mechanism. Do not claim a second account
ran a real worker until the separate second-account GitHub Actions / PR
acceptance succeeds. No recurring Autopilot job or paid model is enabled.
