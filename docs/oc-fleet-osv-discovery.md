# OSV candidate discovery (read-only)

This is a SHA-bound, **read-only** observer for the public ADXL355 repository.
It does not call a coding model, trigger a worker, create a branch or PR, push
commits, merge PRs, change systemd, or mutate repository permissions.

The observer checks a bounded set of owner-authored, same-repository PRs
targeting main. A PR qualifies for further consideration only if it changes
exactly one existing file (node/package-lock.json), and the immutable PR
HEAD has exactly two terminal failed GitHub Actions check runs: the OSV
repository scan and the aggregate Dependency Audit. Both must reference
the same authentic workflow run and job IDs. No other current-SHA check may
be pending or failed; successful checks are included in the proposed policy
for independent post-repair verification.

The official workflow's one, unexpired OSV SARIF artifact is inspected.
Only one structured CVE result against the lockfile is acceptable.
The advisory must be reviewed, non-withdrawn, high/critical, match the CVE
and vulnerable npm dev dependency, and include an eligible patch-level
fix. The current PR HEAD must still contain exactly the reported vulnerable
version. The existing scoped publisher then independently revalidates the
PR identity, run provenance, reviews, check statuses and artifact source.

The observer also checks the most recent main-branch Dependency Audit for
the current main SHA. A successful audit yields MAIN_AUDIT_GREEN. A failed
run yields MAIN_AUDIT_FAILED_REVIEW, which is **not** authorization to
create a PR. A pending run is never overridden by an older successful run.
Stale historical audit failures are not revived.

## Usage

Read-only terminal result:

    python3 .github/scripts/oc_fleet_osv_discover.py

Optionally write SHA-specific candidate policies in an owner-private
directory for independent operator review:

    python3 .github/scripts/oc_fleet_osv_discover.py --policy-dir ~/.local/state/oc-fleet/osv-candidates

The default does not write files. A candidate policy is a proposed
decision, not a GitHub write authorization. Publication through
oc_fleet_osv_publish.py requires a separate explicit --run and a fresh
full verification. This observer is **not integrated into the five-minute
live dispatcher**; that integration awaits separate acceptance.

## Initial live observation, 20 October 2026

Current ADXL355 main HEAD:
2a8ad55de259688e77ff73d24a5ec204a598cd4b.

Current Dependency Audit workflow run 39436214279: success.
Open eligible PRs: 0. The historical already-fixed
GHSA-68fv-2mgg-jv7q incident was not selected.
