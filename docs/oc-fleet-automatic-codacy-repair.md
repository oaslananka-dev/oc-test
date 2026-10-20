# Automatic selection: previously proven Codacy bare assertion rule

This is a deliberately **one-category** automatic production CI repair
extension, not a generic AI fixer. It reuses the existing independent,
SHA-bound publisher, unprivileged OpenCode worker, durable SQLite receipt,
two-attempt limit, and required green checks. It never automatically merges.

**Target:** public oaslananka/adxl355, only existing PRs authored by
oaslananka with branches inside the same repository, targeting main.

**Known issue category:** the Codacy GitHub App (id 56611) reports
action_required and exactly one specific Python bare-assert annotation
("Use of assert detected. The enclosed code will be removed when compiling
to optimised byte code.") on a newly added assert line in
python/tests/test_spidev_adapter.py. The PR must modify only that single
Python test file, with at most 40 added and 10 removed lines. This rule
is grounded in the successfully repaired ADXL355 PR #128:
https://github.com/oaslananka/adxl355/pull/128

This does not authorize other Codacy rules, test failures, dependency
issues, review comments, workflow errors, other files or other repositories.

For selection, every current-SHA check must be completed, and exactly
one check must be unsuccessful, named Codacy Static Code Analysis,
from the pinned GitHub App, with the exact annotation and line mapping.
Ten pinned required checks must exist; all other required checks must
already be successful. Reviews, commit statuses and the PR HEAD are
independently rechecked using the proven scoped-repair controls.

The repair goal is a constant operator-reviewed sentence; untrusted PR
descriptions, issue comments, job logs and annotation messages are never
forwarded into an AI prompt.

At most one new worker can be selected per polling cycle. The controller
checks active GitHub worker workflow runs (queued, in_progress, waiting,
requested) and refuses to exceed the existing two-worker budget.
Mode-0600 policies are saved under
~/.local/state/oc-fleet/auto-policies/ and the existing
~/.local/state/oc-fleet/scoped-repairs.sqlite3 receipt is reused.
Unreconciled RESERVED/DISPATCHED runs block retries. PUBLISHED runs
are reconciled against fresh CI first. Prior FAILED and CI_SUCCEEDED
receipts are not silently retried. Existing policies are never overwritten
if the SHA has changed.

## Operation

The default command is read-only:

    python3 .github/scripts/oc_fleet_auto_repair.py

The timer enables live execution only with the explicit
--auto-codacy-bare-assert flag. It never changes branch protection or
merges PRs. A second category requires a separate proven rule and tests.

## Live autonomous production acceptance — 20 October 2026

ADXL355 PR #129 added a useful hardware-free test verifying that
closing an unopened Linux SPI transport does not allocate a hardware
device. The initial commit was
cf859e82ede8a4a0c640e4393321ce63a1118eaf. The only unsuccessful
CI check was Codacy Static Code Analysis, conclusion action_required,
run ID 118665202259, App ID 56611, with exactly one approved bare-assert
finding on newly added line 114 of python/tests/test_spidev_adapter.py.

The 16:50 systemd timer did not dispatch while other checks were
pending. With all other checks green, the 16:55 timer automatically
selected and verified PR #129 and dispatched the free-model worker:
https://github.com/MakerParsDev/oc-test/actions/runs/39434643165
The worker completed successfully. The trusted publisher verified the
artifact and fast-forwarded the existing PR branch to
e311ed5e2b95260123225a6314ecec1a4aa36843, with the exact original
SHA as the sole parent. The patch replaced just one newly introduced
bare assert with self.assertEqual(factory.call_count, 0). No force-push,
replacement PR or auto-merge occurred.

At 17:00, the timer correctly deferred CI reconciliation while fresh
checks were still pending. The new HEAD then received 47 successful
and 1 neutral checks, with no failures or pending jobs. At 17:05,
the systemd timer autonomously verified the new checks and updated
the durable receipt from PUBLISHED to CI_SUCCEEDED. Only one worker
run was used, without retries. After independent review, the useful
test PR #129 was deliberately squash-merged by the operator:
https://github.com/oaslananka/adxl355/pull/129
Merge commit: 2a8ad55de259688e77ff73d24a5ec204a598cd4b.

**Acceptance: PASS** for this one precise Codacy test-file rule.
This does not authorize other lint rules, arbitrary failing CI jobs,
untrusted review comments, dependencies or merge conflicts. The
2-worker limit, SHA binding, immutable artifact verification, duplicate
receipt handling and no-auto-merge policy remain active.
