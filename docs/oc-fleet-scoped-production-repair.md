# Explicit scoped repair of one production PR

This is an **operator-selected**, non-scheduled repair path for the public
oaslananka/adxl355 Python driver. It does not enable general automatic
repairs, arbitrary log-following, comment-driven prompts, release workflows,
automatic merge, or modifications outside python/src/adxl355/ and python/tests/.

The owner selects an already-open PR and stores one JSON policy as a mode-0600
file. Example structure with ILLUSTRATIVE, not live, identities:

~~~json
{
  "repository": "oaslananka/adxl355",
  "pr": 135,
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "head_branch": "fix/python-regression",
  "check_name": "Python (3.12)",
  "check_run_id": 123456789,
  "check_app_id": 15368,
  "check_conclusion": "failure",
  "required_green_checks": ["Python (3.12)", "Semgrep"],
  "allowed_paths": [
    "python/src/adxl355/adapters/spidev.py",
    "python/tests/test_spidev_adapter.py"
  ],
  "repair_goal": "Correct exception-path handle cleanup and exercise retry in tests."
}
~~~

The identifiers must come from the real GitHub PR and its matching failed
check run; the example is not a real authorization.

Operator workflow from the controller checkout:

~~~bash
python3 .github/scripts/oc_fleet_scoped_repair.py --policy /path/to/approved.json
python3 .github/scripts/oc_fleet_scoped_repair.py --policy /path/to/approved.json --run
python3 .github/scripts/oc_fleet_scoped_repair.py --policy /path/to/approved.json --verify-ci
~~~

The first call reads and authorizes; it never dispatches. The second requires
an explicit --run: an allowlisted GitHub-hosted worker generates a patch on
an immutable checkout without publishing credentials. The trusted publisher
checks worker artifact/run identity, hash, changed paths and live PR SHA,
then appends a normal fast-forward commit on the existing PR branch.
No new PR, force-push, bypass or automatic merge. The last call verifies
CI and exact policy-required green checks on the new SHA, without merging.

Durable SQLite states:
RESERVED -> DISPATCHED -> PUBLISHED -> CI_SUCCEEDED.
A proven failed worker may go DISPATCHED -> FAILED. At most two attempts per
PR; ambiguous outcomes are retained for owner reconciliation, not retried
blindly. PUBLISHED does not mean the new CI passed.

The prerequisite snapshot must have exactly one completed failing check with
the pinned publisher App ID, check ID, name, commit SHA and no additional
pending/failing checks, conflicts or blocking review/inline comments.
Repository access is read from the preexisting allowlist without modifying
secrets. Check names are not diagnosed as root causes; repair goals come from
the owner's local policy, never bot comments or untrusted CI logs.

Status 20 Oct 2026: no open ADXL355 PRs, so there is no live production target.
This mode is NOT wired into the existing five-minute timer. Offline mocked
and bare-Git tests alone do not establish a production acceptance.
The previously validated F1 repair remains unchanged.
