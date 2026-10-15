# Nerlex fleet pilot: curated toolchain (Frontier disabled)

GitHub issue: https://github.com/oaslananka/nerlex/issues/26
Multica issue: OAS-9 in oaslananka workspace.
Worker: oaslananka-dev/oc-test ephemeral GitHub Actions runner.
Publisher: already-authorized gh CLI account on amd2700x.

To preview the validated dispatch:

    python .github/scripts/oc_fleet_issue.py run --repo oaslananka/nerlex --issue 26 --path tests/test_hashing.py --profile python-ruff --integration codacy --integration sonarqube --integration context7 --dry-run

Integration IDs can be repeated: context7, codacy, sonarqube. No arbitrary
commands, custom scripts, remote URLs or npm package inputs are accepted
from task data.

Context7: Official OpenCode v2 @upstash/context7-opencode@0.2.1 plugin
bundles a remote MCP and skill. The worker reads CONTEXT7_API_KEY only
from Doppler project oc-test, config main, via the read-only Actions bootstrap token. Missing
key produces a clear fail-closed error; no headless OAuth is attempted.
Never store keys in issues, PRs, model logs or committed configuration.

Codacy: the worker installs vetted @codacy/analysis-cli@0.24.0 for
local analysis. Before PR publication, the trusted amd2700x controller
also runs local Codacy analysis for changed paths using its existing login.

SonarQube: the trusted amd2700x controller runs already-authenticated
SonarQube CLI 1.9.0 before PR publication against registered project
oaslananka_nerlex. The ephemeral model process gets NO Sonar credential.
This pilot does not install or authenticate Sonar CLI inside the runner.
A failed or unavailable quality scan prevents publication, never silently
counts as success. No analysis upload is performed by Codacy.

LSP: OpenCode V2 currently accepts lsp=true but has NO functional LSP
runtime. Use Ruff, mypy, pytest and build tools for real diagnostics.
We intentionally avoid a useless LSP flag.

The controller independently checks native run identity, successful jobs,
immutable source SHA, artifact digest and changed-path scope, then runs
selected scans before using its own gh identity to create a PR.
No automatic merge, PR repair, or privileged agent credentials.

Store CONTEXT7_API_KEY only in Doppler oc-test/main. GitHub Actions should
retain only the read-only DOPPLER_TOKEN bootstrap repository secret.
