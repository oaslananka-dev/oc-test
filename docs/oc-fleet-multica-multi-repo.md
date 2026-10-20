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
    fleet_target_issue: "124"
    fleet_target_account: oaslananka
    fleet_worker_repo: MakerParsDev/oc-test
    fleet_worker_account: MakerParsDev
    fleet_allowed_path: python/tests/test_spidev_adapter.py
    fleet_allowed_paths:
      - python/src/adxl355/adapters/spidev.py
    fleet_external_pr_only: true
    fleet_auto_merge: false
    fleet_ready: true

Optional fleet_allowed_paths may contain up to 11 extra entries, for a
maximum of 12 approved files/subtrees per issue. Paths must be relative
and safe. Hidden files, .git, .github, node_modules and parent traversal
are never permitted. Non-pilot repos must use fleet_external_pr_only=true.
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

The systemd deployment raises the pilot ceiling from 2 to 4 for a
controlled scale-up. Ephemeral workers remain unprivileged. Only the
trusted publisher can create a branch/PR. Uncertain publication remains
claimed for manual reconciliation, not a blind second dispatch.

## Acceptance boundaries

- OAS-14 through OAS-18 established live Multica routing and verified
  PRs, with two parallel real tasks.
- Offline tests prove four unique task admissions, bounded worker
  capacity, no claims at full capacity, and multi-repo scoped routing.
- On 20 October, a read-only check found four free worker slots with
  an empty Multica queue.
- A full four-worker end-to-end run, cross-repo lifecycle completion,
  and higher-than-four capacity remain unverified. Do not claim
  general any-repo or twenty-worker production acceptance.
- Private checkout, multi-account quota balancing, arbitrary CI
  repair and external-repo automatic merge are not enabled.

After a live four-task pilot passes its GitHub run/PR/CI receipts,
increase capacity to eight, then toward verified plan and model limits.
The Multica job flow does not require an open ChatGPT session.
