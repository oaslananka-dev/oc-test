# ocfilo pilot: separate verified-artifact PR publisher

The OpenCode worker remains unprivileged. The publisher is a separate
GitHub Actions workflow in the reviewed main branch. It uses a GitHub App
installation token to publish a pull request without merging.

## Opt-in pilot scope
- Only repository: oaslananka-dev/oc-test. Only main source head SHA.
- Only reviewed worker workflow: oc-fleet-worker.yml (not arbitrary Actions run).
- Only successful, full validate and execute jobs. No config-only output.
- Only unexpired native run artifact fleet-RUN-ATTEMPT-TASK.
- Only files below playground/fleet/ (no deletes, symlinks, .github, other paths).
- SHA256, task/run/attempt/repository/immutable head SHA are independently verified:
  before patch application and after git normalizes the exact patch.
- Refuse a stale main SHA, repeated task branch, unknown jobs or artifact layout.
- Create a separate fleet/TASK branch and PR. Never merge or deploy.
- The source result.json is NOT a cryptographic signature; the GitHub source
  run identity, job successes, and expected artifact name are also required.
- No arbitrary target repos, MCPs, plugins, or commands in publisher inputs.

## Setup (once)
Create a fleet-publisher GitHub Actions environment in the runner repository.
Add environment variable FLEET_APP_CLIENT_ID and environment secret
FLEET_APP_PRIVATE_KEY for an approved GitHub App installation, installed on
oaslananka-dev/oc-test with Contents: write and Pull requests: write.
The token is minted for only oc-test and revoked automatically at job end.
Use environment reviewer rules if required by your release policy. Never give
the App token or private key to the worker or its untrusted MCP tools.

## Pilot run
Merge this PR after baseline, publisher-contract and Semgrep pass.
After a new read-only execute run succeeds on that merged main SHA, first dispatch
oc-fleet-publish.yml on main with source_run_id, task_id and run_mode=verify-only. This executes source-run, artifact and scoped-patch validation with a read-only token; it will not create a branch or PR. Once the GitHub App publisher credentials are configured, repeat with run_mode=publish.
Inspect the resulting PR and CI. Do not merge until checks/review pass.

Older OAS-3 and OAS-6 artifacts cannot be published by this job after main
advanced; this prevents stale patch publication. No actual automatic
publisher execution or auto-merge is claimed until a live run is verified.

## Future milestones (not yet implemented)
- Trusted dispatcher requests publication only for publish-authorized issues.
- Multi-target GitHub App installations and allowlisted target scopes.
- CI/review observer and bounded repairs on the existing PR branch.
- Merge only after required checks, reviews and thread resolution.
