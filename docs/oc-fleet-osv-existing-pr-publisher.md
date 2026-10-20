# OSV: explicitly selected, SHA-bound existing PR publisher

This deterministic, model-free component is separate from the
automatic Codacy bare-assert selector and **not** scheduled by the
five-minute systemd timer. It does not create PRs, force-push, merge,
modify workflow/security policies, or authorize unattended OSV repairs.

Only the existing public oaslananka/adxl355 PR is eligible when a mode-0600
operator policy pins a currently open, owner-authored, same-repository
PR and main base, exact SHA and branch, the failing Dependency Audit
workflow run, both GitHub Actions check-run IDs, a reviewed GHSA and
the names of required green checks.

The failed SHA must have exactly two completed failures:
OSV repository audit / osv-scan and the aggregate Dependency Audit check.
Both must come from the **same pinned pull_request workflow run**.
Every other current check must be terminal and successful, neutral or skipped;
no review change request, inline feedback, merge conflict or failed commit
status can be present. The workflow's GitHub artifact must be a single
unexpired OSV Scanner SARIF file with one CVE affecting node/package-lock.json.

The publisher revalidates the authentic official reviewed advisory,
same-major/minor patch version, npm lockfile-v3 and one dev-only transitive
dependency. It runs npm update with --package-lock-only, --ignore-scripts,
--no-audit, --no-fund in a fresh detached Git checkout. **Only three fields**
(version, resolved, integrity) of one lockfile node may change. Any
additional file/package change fails closed.

After rechecking the unchanged PR SHA it publishes exactly one non-forced
commit to the **existing PR branch**, as the trusted owner. SQLite records
RESERVED before publishing, PUBLISHED only after a verified fast-forward,
and CI_SUCCEEDED only after verifying fresh successful required check-runs
on the newly published SHA. An uncertain push stays RESERVED for owner
reconciliation rather than an automatic retry.

An owner-private JSON policy has this illustrative structure (IDs are
examples, not valid approvals):

~~~json
{
  "repository": "oaslananka/adxl355",
  "pr": 173,
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "head_branch": "fix/osv-lockfile-security",
  "workflow_run_id": 123456789,
  "osv_check_run_id": 400001,
  "aggregate_check_run_id": 400002,
  "advisory_id": "GHSA-68fv-2mgg-jv7q",
  "required_green_checks": [
    "OSV repository audit / osv-scan",
    "Dependency Audit",
    "Node.js (24)"
  ]
}
~~~

Usage in a trusted operator checkout:

    python3 .github/scripts/oc_fleet_osv_publish.py --policy /path/to/private-policy.json
    python3 .github/scripts/oc_fleet_osv_publish.py --policy /path/to/private-policy.json --run
    python3 .github/scripts/oc_fleet_osv_publish.py --policy /path/to/private-policy.json --verify-ci

The default invocation is read-only and must not reserve or publish.
Neither --run nor --verify-ci automatically merges. After publication,
CI_SUCCEEDED means checks passed, not that merge was authorized.

## Historical validation, 20 October 2026

The actual original GitHub Actions OSV artifact from failed ADXL355
Dependency Audit run 39388608580, the official reviewed advisory
GHSA-68fv-2mgg-jv7q / CVE-2026-93749, and historical pre-fix commit
cd8512dd410bdf4794ace00ee1d94ecc7054a5da reproduced the exact
source-map-js 1.2.1 to 1.2.2 lockfile patch. Node typecheck and all
81 tests succeeded. A local bare-Git acceptance then demonstrated
same-branch fast-forward without a second branch, new PR, or forced push.

The real issue was already fixed by ADXL355 PR #126 and main is green.
**No new vulnerability exists for this case**, and no live OSV PR has
been opened or modified by this publisher. A future real eligible
PR still requires its own end-to-end GitHub publishing and CI acceptance.
