# Nonblocking, restartable Multica worker runs

Multica remains the authoritative job queue. AMD2700X's existing
five-minute systemd timer remains the only poller; GitHub-hosted OpenCode
workers remain unprivileged and use free models.

Previously the controller waited up to 1900 seconds inside the locked
systemd service for each worker. Four simultaneous workers passed the
20 October production acceptance (OAS-19..22, four PRs #85..88), but
one slow worker delayed further polls.

## Two-stage state machine

1. Dispatch: operator-authored ready Multica tasks and live GitHub Actions
   capacity are checked. A mode-0600 local atomic claim is created, and
   exactly one worker is launched by the existing trusted controller
   with --dispatch-only. The original durable worker receipt is checked.
   Multica receives fleet_run_id, fleet_ready=false,
   fleet_result=worker_dispatched and status in_progress.
   The timer finishes without waiting for OpenCode.
2. Later reconciliation: Only the pinned GitHub Actions workflow run is
   checked. Pending runs remain untouched. Successful completed workers
   are processed by the existing trusted controller's resume path,
   which independently verifies run, SHA, artifact, permitted file patch
   and publishing account. The resulting PR is recorded in Multica,
   with status in_review. Existing pilot CI/review finalization may
   then merge and mark done. Failed workers become blocked and are not
   blindly relaunched.

On a crash during metadata updates, the local original worker receipt
and atomic claim must match the issue and its GitHub repo, scope, worker
and run identity before recovery. Unknown claims remain claimed and
need reconciliation. Uncertain PR publication is not retried blindly.
One blocked result is logged and does not stop admission of another
ready task.

Pilot auto-merge waits while a worker result remains unreconciled.
This prevents merge on main from invalidating slower sibling workers'
immutable base SHA. A slow worker may delay merge, but no longer blocks
new worker admission. Nonpilot target repos stay PR-only; the concurrency
operator budget remains four.

## Running

Read-only, including recovery:

    python3 .github/scripts/oc_fleet_multica_poll.py --detached \
      --project-id 0506e0e5-01a7-4de9-b63b-67e993809721 --max-parallel 4

The systemd service enables --run --detached while retaining the pilot
F1 and automatic Codacy checks. No new keys, permissions or model costs.

The detached controller has offline negative tests, simulated receipt recovery,
single-worker status checks and local PR publication. Its first real
end-to-end acceptance was completed on 20 October 2026.

## Verified live acceptance: OAS-23 / GitHub issue #90

Scope: public pilot repository `oaslananka-dev/oc-test`; exactly one
`playground/fleet/detached-20261020-epsilon.json` file; free
`opencode/big-pickle` model; worker `MakerParsDev/oc-test`. The deployed
controller was on `6a11f48354fdbb416442aabb90fac25b2e7bb7ce`, with
`--detached --max-parallel 4` and an active five-minute systemd timer.

1. A Multica task OAS-23 was created in `backlog`, initially
   `fleet_ready=false`, and passed the controller's dry-run checks.
   It was then set ready.
2. The 19:20:01 (Europe/Istanbul) **scheduled** service tick dispatched
   GitHub Actions run
   [39456535682](https://github.com/MakerParsDev/oc-test/actions/runs/39456535682)
   at 19:20:22, and successfully exited at 19:20:28 while the worker
   was still running. Multica recorded `in_progress`,
   `fleet_result=worker_dispatched` and the exact run ID.
3. The run's `validate` and `execute` jobs succeeded (attempt 1),
   producing artifact `fleet-39456535682-1-gh-90-105338fff979`.
   The **next scheduled tick** at 19:25:07 reconciled that original run,
   published [PR #91](https://github.com/oaslananka-dev/oc-test/pull/91)
   at 19:25:32, and moved OAS-23 to `in_review`.
4. PR #91 added only the approved fixture (6 lines); `baseline`
   and `semgrep-cloud-platform/scan` succeeded. The 19:25 tick
   had conservatively deferred merge while Semgrep was pending.
5. After checks completed, the operator **manually invoked the
   existing deployed systemd service**, not a bypass. Its pilot
   finalizer recorded `FLEET_DONE OAS-23 91` at 19:28:34.
   PR #91 was merged with commit
   `7b6f4e89142e86696ae6b6f410a0af6da7125ce8`; GitHub
   issue #90 closed, the file was verified on main, and Multica
   recorded `done`, `fleet_result=completed`.
6. A separate subsequent service cycle finished successfully with
   zero eligible issues. OAS-23 still had the original worker run
   and PR number; exactly one worker run and one PR were observed
   for this fixture. PR #1 and PR #2 remain untouched.

Offline regression tests on the installed controller: **195/195 PASS**.

This is evidence for **one** real detached pilot lifecycle, not proof of
arbitrary-repository auto-merge, private-repo support or more than four
concurrent workers. Production/external repositories remain PR-only.

## Two-task detached queue acceptance: OAS-24 / OAS-25

Verified on 20 October 2026 in the public, pilot-only
`oaslananka-dev/oc-test` target repo; both tasks used free
`opencode/big-pickle` on `MakerParsDev/oc-test` GitHub-hosted workers.
The controller used `--detached --max-parallel 4`, without a second queue
or additional privileges.

1. GitHub issues [#93](https://github.com/oaslananka-dev/oc-test/issues/93)
   and [#94](https://github.com/oaslananka-dev/oc-test/issues/94)
   had disjoint, explicitly allowlisted JSON fixture paths. Multica
   OAS-24 and OAS-25 each passed dry-run validation before being marked
   ready at 19:39:16 (Europe/Istanbul).
2. The **19:40 scheduled** dispatcher detected two eligible tasks and
   launched runs
   [39459504096](https://github.com/MakerParsDev/oc-test/actions/runs/39459504096)
   for OAS-24/#93 and
   [39459499711](https://github.com/MakerParsDev/oc-test/actions/runs/39459499711)
   for OAS-25/#94. Both ran concurrently, completed with `SUCCESS`,
   and produced separate SHA/run/issue-bound artifacts (attempt 1).
   The systemd dispatcher exited at 19:40:35, without waiting for them.
3. The **19:45 scheduled** reconciliation used those same run IDs,
   published [PR #95](https://github.com/oaslananka-dev/oc-test/pull/95)
   and [PR #96](https://github.com/oaslananka-dev/oc-test/pull/96),
   and moved both Multica tasks to `in_review`. Each PR changed exactly
   its own six-line JSON fixture. `baseline` and Semgrep passed on
   each PR; reviews, inline comments and issue comments were empty.
4. The **19:50 scheduled** finalizer merged PR #95 with commit
   `f71ef7459936875151bdc4cd38e4021442902934`, marking OAS-24
   `done / completed`. It processed only one eligible merge per tick.
5. The **19:55 scheduled** finalizer merged PR #96 with commit
   `57f4ecc9b34dc1551781264401fbde4f58d80fba`, marking OAS-25
   `done / completed`. Both GitHub issues closed; both JSON files
   were independently verified on `main`.
6. At final verification, exactly **two** new GitHub worker runs and
   exactly **two** corresponding PRs were observed, without duplicates.
   The original test PR #1 and #2 remained open; the five-minute timer
   was still active, with an empty eligible queue.

The installed controller's offline suite passed **195/195** tests.
This demonstrates a bounded two-task **detached** queue lifecycle and
sequential, unattended pilot merges. It is **not** acceptance for
higher-than-four concurrency, multi-worker-account load balancing,
non-pilot auto-merge or arbitrary-repository execution.
