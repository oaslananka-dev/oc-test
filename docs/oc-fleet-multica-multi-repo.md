# Multica-centered, multi-repository GitHub worker admission

Multica remains the sole job queue. The owner marks Multica issues
ready; a five-minute systemd controller on AMD2700X reads the queue,
allocates capacity and dispatches ephemeral GitHub Actions/OpenCode
workers. No live ChatGPT conversation or native Multica AI agent is
required. The actual OpenCode execution remains on GitHub-hosted
workers, using free configured models.

## Authorize a target

A Multica issue routes to an existing open GitHub issue through
owner-prepared metadata. This is not permission to use arbitrary
repositories. The target must already be present in Doppler oc-test/main
FLEET_ALLOWED_REPOS, the worker in FLEET_WORKER_REPOS, and both chosen
GitHub accounts authenticated. Public, target-writable repos only.

Example of the Multica issue metadata:

    fleet_target_repo: oaslananka/adxl355
    fleet_target_issue: "130"
    fleet_target_account: oaslananka
    fleet_worker_repo: MakerParsDev/oc-test
    fleet_worker_account: MakerParsDev
    fleet_allowed_path: python/tests/test_spidev_adapter.py
    fleet_allowed_paths: '["python/src/adxl355/adapters/spidev.py"]'
    fleet_external_pr_only: true
    fleet_auto_merge: false
    fleet_ready: true

Optional fleet_allowed_paths may contain up to 11 extra entries, for a
maximum of 12 approved files/subtrees per issue. Multica V1 issue metadata
accepts only primitive types: its CLI stores JSON array values as strings.
Set this field with the literal CLI value
`--value '["python/src/adxl355/adapters/spidev.py"]'`. Since
[controller PR #98](https://github.com/oaslananka-dev/oc-test/pull/98),
the trusted controller decodes only a valid JSON array of safe, explicit
paths and uses the same normalized scopes in dispatch and reconciliation.
An invalid, duplicate or overlapping scope fails closed; JSON-looking
metadata is never execution authorization by itself. Paths must be
relative and safe. Hidden files, .git, .github, node_modules and parent
traversal are forbidden. Non-pilot repos must use
fleet_external_pr_only=true.
External-repo auto-merge/finalization is not enabled; user CI/review
completion requires a separate acceptance.

The existing pilot oaslananka-dev/oc-test keeps its original metadata
and explicitly opted-in test PR merge finalizer.

## Admission and actual limit

The dispatcher supports an operator-configured ceiling of 1 to 20 tasks
per cycle; this is not a claim to arbitrary GitHub concurrency. It
reads active GitHub Actions worker run counts for queued, in_progress,
waiting and requested states. The number of tasks admitted to each
worker repo cannot exceed ceiling - current active worker runs.

When a worker pool is full, the controller leaves the Multica issue
unclaimed. Incomplete GitHub API evidence blocks the dispatch. Local
claims and trusted publication receipts prevent accidental repeats.
Different worker repos can be counted independently, but total account
quota and free-model provider limits are not yet globally balanced.

The deployed bounded operator ceiling is 4 concurrent workers. Ephemeral workers remain unprivileged. Only the
trusted publisher can create a branch/PR. Uncertain publication remains
claimed for manual reconciliation, not a blind second dispatch.

## Live acceptance and remaining boundaries

- OAS-14 through OAS-18 established authenticated worker routing, PR
  publication, CI and pilot finalization.
- OAS-19 through OAS-22 validated four concurrent GitHub-hosted workers,
  four distinct scope-verified PRs #85–#88, green checks, protected merges,
  closed GitHub issues and completed Multica records on 20 October 2026.
  The four-worker test used the previous synchronous controller.
- Detached controller acceptance OAS-23 / GitHub #90 / PR #91 proved a
  nonblocking dispatch, later scheduled reconciliation and bounded
  merge completion. OAS-24 and OAS-25 / PR #95 and #96 proved two
  detached workers followed by separate PRs and serialized scheduled
  finalizer merges. See [detached acceptance evidence](oc-fleet-multica-detached.md).
- First real **external-repository PR-only publication**: Multica OAS-26,
  [ADXL355 issue #130](https://github.com/oaslananka/adxl355/issues/130),
  successful [worker run #39464471583](https://github.com/MakerParsDev/oc-test/actions/runs/39464471583),
  artifact `fleet-39464471583-1-gh-130-ebd21d943d61`, and
  [PR #131](https://github.com/oaslananka/adxl355/pull/131).
  The trusted publisher changed only the two declared Python SPI
  implementation/test paths. Multica moved to `in_review`; the external
  finalizer reported `not_authorized`, as required. This proves PR-only
  delivery, **not automatic merge** or closure of GitHub issue #130.
  The original worker commit was `b1de64bd`. During PR review,
  Codacy flagged four new bare assertions in tests; one separate,
  bounded review commit `faf717df` fixed these without removing or
  rewriting the original worker commit. The final PR HEAD
  `faf717dfffa6be9dd44d039aa68a79a7466c41dd` passed 47 GitHub
  checks, with one Mergify queue check intentionally skipped and zero
  pending or failed checks; Python 191/191 passed locally. Codacy
  reported zero new issues, Sonar quality gate passed with 100% new
  code coverage, and all bot/review comments were examined. PR #131
  remains `OPEN / CLEAN`, issue #130 remains open, and target `main`
  remains at `2a8ad55de259688e77ff73d24a5ec204a598cd4b`.
- External tasks stay `in_review` for their own review/merge decision;
  no untrusted or generic target-repository auto-merge permission exists.
  Publishing a PR is not the same as merging or deploying it.
- Higher-than-four concurrency, multi-worker-account quota balancing,
  private repository checkout, general CI self-repair and external-repo
  automatic merging are still **not validated or enabled**.

Retain the verified ceiling of four until measured GitHub-hosted runner
capacity and free-model quota support a controlled increase. Multica
remains the queue; no open ChatGPT session is required for polling.

## Optional safe target-base advance (#104)

This switch is **disabled by default**. For an already-authorized **external
PR-only** task, the operator may set Multica boolean metadata
`fleet_allow_safe_base_advance=true` **before** setting `fleet_ready=true`.
It is not compatible with automatic merge, unreviewed target repositories,
retry-failed flows or normal post-dispatch authorization changes.

The original trusted dispatch receipt records the boolean decision, the
target and worker account identities, and the external-PR-only restriction.
On detached resume, live Multica metadata must match this original receipt
exactly. Both attempts to enable and disable the policy after dispatch fail
closed; old receipts without the field are interpreted as **false**.

If target `main` advances, the publisher verifies the original worker's
GitHub Actions result and exact scoped patch, checks monotonic Git ancestry,
compares original and current Git object IDs for every approved path and
directory, and requires the authenticated artifact patch SHA-256 to remain
identical when applied to the new base. It rejects changed scoped objects,
divergent/force-pushed history, unexpected actors, broad/incomplete changes,
duplicate task branches and a branch tip that moves again before publication.
All normal PR checks and review requirements still apply. No worker model
call or additional GitHub permissions are required for this recovery.

A successful **offline** Git-history and dispatch-policy test does not by
itself establish a live end-to-end recovery acceptance. Existing tasks remain
fail-closed unless expressly opted in **at dispatch**.

## Live safe-base recovery acceptance: OAS-31/OAS-32 (21 October 2026)

This is the first real GitHub-hosted two-sided live acceptance of the
optional SHA/base-advance recovery policy for external PR-only tasks.

- The trusted controller's own source repo cannot serve as its external
  PR-only target because the existing source-repository policy prohibits
  self-targeting in that mode. No exception was added. The previously
  authorized target oaslananka/adxl355 was used instead.
- Multica OAS-31 / [target issue #140](https://github.com/oaslananka/adxl355/issues/140)
  was restricted to playground/fleet/base-recovery-20261021-positive.json.
  OAS-32 / [issue #141](https://github.com/oaslananka/adxl355/issues/141)
  was restricted to playground/fleet/base-recovery-20261021-negative.json.
  Both enabled fleet_allow_safe_base_advance=true BEFORE dispatch;
  fleet_external_pr_only=true, fleet_auto_merge=false and free
  opencode/big-pickle were preserved.
- The 13:10 scheduled tick dispatched distinct GitHub-hosted workers:
  [39582617823](https://github.com/MakerParsDev/oc-test/actions/runs/39582617823)
  (OAS-31) and
  [39582617771](https://github.com/MakerParsDev/oc-test/actions/runs/39582617771)
  (OAS-32). Each completed SUCCESS, run_attempt=1. Both original
  trusted controller receipts pinned checkout_sha
  1a9bf4330021349a6d955d85a836a7b4684d0ffa,
  the account identities, optional dispatch policy and exact scopes.
- Base fixture [PR #142](https://github.com/oaslananka/adxl355/pull/142)
  initially exposed unrelated pip-audit advisory PYSEC-2026-4320 for
  pinned certifi 2026.7.22. The separate narrow
  [security PR #144](https://github.com/oaslananka/adxl355/pull/144)
  updated the PlatformIO pin and independently verified two PyPI
  artifact hashes to certifi 2026.10.19. Audit and CI passed;
  controlled merge SHA 8f063814ca4571ad871903577f2d4ac4348d3248.
- PR #142 was then updated to the patched main, passed 45 checks and
  merged as 5f47f2a380d4cfe360898963087d9e68c3c7015d.
  Its two harmless fixture files changed a file unrelated to OAS-31
  and exactly the allowed file of OAS-32 (negative conflict).
- At 13:24 the real systemd detached reconciliation published the
  original OAS-31 worker artifact as
  [ADXL355 PR #145](https://github.com/oaslananka/adxl355/pull/145).
  Scope is exactly its one positive JSON file; 46/46 GitHub checks
  SUCCESS. Multica OAS-31 is in_review/pr_created, without auto-merge.
- OAS-32 was refused in the same reconciliation. A separate safe
  resume verify-only invocation confirmed the precise error:
  "target base changed approved scope:
  playground/fleet/base-recovery-20261021-negative.json".
  No negative PR was created and no worker was rerun. Multica records
  OAS-32 as blocked/worker_blocked with the scoped conflict reason.
- The five-minute timer was temporarily paused after both workers
  succeeded to prevent early publication before the reviewed base
  change. It was restored and the 13:26 catch-up tick returned SUCCESS
  without duplicate dispatch or unauthorized external merge.

Result: one safe monotonic base recovery published the original
authenticated patch; one changed approved path failed closed. Worker
capacity stays four; free model only; no broadened permissions,
auto-merge, or unsafe fallback. This is an accepted live test,
not authorization to merge the intentionally open fixture PR #145.
