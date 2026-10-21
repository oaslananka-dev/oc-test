# Optional live worker telemetry to Multica

This is a small, non-native execution progress bridge. It does not create a
Multica native agent run. The coding and GitHub pull-request workflow still
works when telemetry is disabled, the VPS is unreachable, or Multica is down.

## Operator configuration

Doppler project oc-test, config main, is authoritative. Add
FLEET_TELEMETRY_URL only AFTER an authenticated HTTPS ingress for the VPS
receiver is tested; use an HTTPS URL ending in /ingest.
If the setting is absent, the worker retains its existing OpenCode output
and execution flow.

GitHub-hosted execute jobs use OIDC permission id-token:write ONLY, plus
contents:read. This is not a GitHub repository write token.
The receiving service checks a signed GitHub OIDC JWT for:
- audience ocfilo-telemetry and official GitHub Actions issuer
- exact source repository oaslananka-dev/oc-test
- exact .github/workflows/oc-fleet-worker.yml on main
- workflow_dispatch event and run id / run attempt
- an existing, trusted run-to-Multica-issue mapping

The signed identity alone cannot select an issue. Only the authorized
operator may select an existing, active internal issue with
--multica-issue OAS-11 when dispatching a GitHub issue. That option is
validated by the trusted task manifest, not read from the target issue body.
Before a temporary OpenCode worker starts, the trusted GitHub Actions
validation job uses its signed OIDC identity to POST the selected identifier
to /register on the existing bridge. The bridge verifies the JWT,
checks that the issue exists, is active and belongs to the explicitly
configured FLEET_MULTICA_WORKSPACE_ID, and stores the immutable run
to issue binding. The unprivileged model process cannot choose or change
this mapping. Repeated registration to the same issue is idempotent;
reassignment of an existing run is rejected.

Existing manifests with no multica_issue keep the old behavior. If the
HTTPS bridge is unavailable, registration is reported as a non-blocking
warning and the coding/PR workflow proceeds unchanged. The operator can
recover by registering the run manually on VPS:

    python3 oc_fleet_telemetry_bridge.py register --run 12345 --attempt 1 --issue OAS-11

A non-publishing telemetry-probe run also supports explicit multica_issue;
it tests automatic registration with two synthetic events and no model.

## VPS receiver

Python 3.12+ with PyJWT (RS256) and cryptography are required. Run
oc_fleet_telemetry_bridge.py serve --port 18098 first on 127.0.0.1.
Use an independently verified HTTPS reverse proxy / Tailscale Funnel
configuration before enabling Doppler FLEET_TELEMETRY_URL.
Do not publish the loopback receiver over bare HTTP or open its port to
the Internet. The receiver does not need a GitHub App token or Doppler
service token. The VPS Multica CLI remains logged in locally.
Set FLEET_MULTICA_WORKSPACE_ID in the receiver's local systemd environment.
It is a non-secret workspace UUID. The receiver refuses HTTP registration
without this setting. Do not expose the workspace UUID as an agent secret.


The optional --multica flag posts and updates one internal progress comment
per registered run at most around once per minute, using Multica comment
revisions. It does not mention or trigger another agent. Never put this
telemetry in target repository PRs, issues or commits.

## Data and reliability boundaries

Only OpenCode JSONL step/tool type, bounded tool names and numeric
usage/cost fields leave the ephemeral worker. Never send raw prompts,
model messages, reasoning, tool arguments, shell output, env vars or
secrets. Avoid the illusion that free-text redaction always works:
free text is excluded by schema. This deliberately does not show all
model messages like a native Multica Execution Log.

SQLite persists schema-checked events and uses (run, attempt, sequence)
as a deduplication key. Run binding expires after 24 hours.
Multica outages and HTTPS errors never fail the coding task.
The original OpenCode process exit code is preserved via PIPESTATUS[0].
The worker may have incomplete telemetry after network interruptions;
GitHub Actions remains the source of truth for terminal job state and CI.

## Pilot stages

1. Offline normalizer, OIDC rejection and storage tests on Ubuntu CI.
2. VPS loopback-only receiver test with no Multica writes.
3. Configure a verified TLS/OIDC ingress without affecting existing
   Tailscale services, then test a real OIDC-authenticated push.
4. Register one test run, enable FLEET_TELEMETRY_URL in Doppler, and verify
   bounded live updates in an internal Multica issue.
5. Roll out to real tasks only after idempotence, outage and latency tests.

The pilot HTTPS ingress was validated before enabling Doppler's telemetry URL.

## Read-only supervisor observation (phase 1)

An operator or future manager agent with an existing authorized device
connection can query the VPS's local SQLite evidence without obtaining
Multica or GitHub write credentials:

    python3 oc_fleet_observe.py --recent 10
    python3 oc_fleet_observe.py --run 38731018050 --attempt 1 --limit 20

The JSON output is schema-versioned and read only (SQLite mode=ro,
query_only=ON). It includes exact GitHub run/attempt and registered
Multica issue identities, bounded counts, a last-event observation
label, approved tool names/states and numeric usage fields. No raw JSONL,
prompt, source text, command arguments, filenames, environment contents,
tokens or comment identifiers are returned. A missing database is never
created. Run comments and their last update are distinguishable from actual
worker status; github_run_state=not_checked means the caller MUST
separately retrieve authoritative GitHub Actions status and artifact receipts.

This is NOT a network listener, a Multica native execution, an OpenCode
session or a remote-control endpoint. Do not use a last-event type to
classify a worker as hung, running or complete: the current telemetry
database has no per-event receipt timestamp, and ingestion can be delayed.
If source evidence is stale, report unknown rather than guessing.
The VPS account permissions protect this local inspection boundary; any
future network-facing manager API needs its own narrow authentication,
authorization, identity scoping, per-run audit and request rate limits.

### Later: safe intervention contract (NOT ENABLED)

A manager should first propose cancel, pause_at_checkpoint or
redirect_at_checkpoint tied to verified repository, run, attempt, head_sha,
task_id and an operator-defined maximum budget. GitHub run cancellation
can be supported through a separate least-privilege control plane AFTER
an explicit approval and independent end-to-end tests. Pause and redirect
require an actual session/checkpoint-capable agent adapter; the current
isolated OpenCode standalone task cannot safely accept live free-text
instructions. Reject unrecognized actions, wrong identity, expanded
write scope, retry/cost limits or unapproved production operations.
Telemetry must never cause an automatic merge, deployment,
paid-model switch, Multica Autopilot trigger or permission workaround.
