# OSV npm patch replay — independent, local-only category

This is a second, distinct repair category for a real dependency-audit
failure. It is **not** connected to the five-minute dispatch timer.
It never creates/merges a PR, grants credentials, or starts an AI worker.

## Evidence and boundaries

The source must be a saved **OSV-Scanner SARIF** artifact with exactly one
CVE result, reported against node/package-lock.json with a syntactically
valid npm package name and an exact installed package version. The operator
CLI independently fetches the matching **GitHub-reviewed, non-withdrawn,
high/critical security advisory** via gh api /advisories/GHSA-.... The CVE,
GHSA, npm package and patched version must match both data sources.

Eligibility requires an npm v3 lockfile, one vulnerable dev dependency
and a stable **same-major, same-minor patch** fix. A different ecosystem,
multiple vulnerabilities, a major/minor dependency upgrade, unrelated
dependency changes, an unexpected integrity/resolved URL, dirty checkout,
missing archive/advisory or mismatched version all block execution.

It runs npm update PACKAGE --package-lock-only --ignore-scripts
--no-audit --no-fund on an isolated, explicitly selected Git checkout.
After the update, the *entire lockfile* is compared: only the selected
node_modules/PACKAGE node may differ, and only its version, resolved and
integrity fields. The only allowed changed path is node/package-lock.json.
No shell interpolation of SARIF or package metadata, no publishing
credentials and no forced writes.

## Historical acceptance — 20 October 2026

ADXL355 nightly Dependency Audit failed for multiple days because OSV
identified GHSA-68fv-2mgg-jv7q / CVE-2026-93749 on the indirect npm
dev dependency source-map-js@1.2.1. The dependent aggregate Dependency
Audit job also failed. GitHub Advisory Database confirms version 1.2.2
as the reviewed fix. This problem is **already resolved on main** via
ADXL355 PR #126: https://github.com/oaslananka/adxl355/pull/126

A past clean pre-fix commit cd8512dd410bdf4794ace00ee1d94ecc7054a5da,
the genuine GitHub Actions OSV SARIF from run 39388608580, and the live
GitHub advisory were used for a deterministic replay. The command
updated *only* the three fields of source-map-js under
node/package-lock.json, reproducing the merged fix's exact lockfile
diff. npm ci with scripts disabled, TypeScript typecheck and all 81 Node
tests passed. The original current main tree was never modified.

## Usage

Default is read-only; choose a clean, isolated historical/current
checkout and download the relevant SARIF from the matching CI run.

    python3 .github/scripts/oc_fleet_osv_lockfile.py \
      --checkout /tmp/selected-adxl355-checkout \
      --sarif /tmp/verified-osv-report.sarif

Apply **locally only**, after review:

    python3 .github/scripts/oc_fleet_osv_lockfile.py \
      --checkout /tmp/selected-adxl355-checkout \
      --sarif /tmp/verified-osv-report.sarif --apply

No auto PR, no scheduled worker, and no merge. A future GitHub publishing
integration needs fresh live error evidence, current HEAD binding,
idempotent reservation, CI gating and an explicit acceptance test; this
historical replay alone does not authorize automatic production changes.
