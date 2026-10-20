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

Acceptance status: the underlying scoped worker/publisher completed a
real production PR repair with successful fresh CI. This exact automatic
selector has mocked regression tests and live read-only observation only;
it is not yet proven to have autonomously selected a second live PR.
