# Configuration and repository-facing metadata

## Source of truth

Doppler project: oc-test
Doppler config: main

Maintain runtime secrets and routing settings in Doppler. GitHub Actions
keeps exactly one bootstrap repository secret, DOPPLER_TOKEN, which is a
read-only service token for this Doppler config. Never put this token in
workflow inputs, prompts, checkout files, PR text or logs.

Currently used Doppler keys:
- CONTEXT7_API_KEY: API key for the optional Context7 documentation plugin
- FLEET_ALLOWED_REPOS: comma-separated trusted GitHub repository allowlist

The Windows operator uses its already-authenticated Doppler CLI for
explicit configuration writes. Temporary Actions jobs read only the
required values using the read-only service token. Do not grant the
OpenCode process DOPPLER_TOKEN or pass the entire Doppler environment
to the model. Only the selected Context7 key is exposed to its plugin
process, and only when the task opted in to Context7.

A manual read-only GitHub Actions workflow, "Verify configuration access",
tests Doppler connectivity and the presence of CONTEXT7_API_KEY. It does
not execute a model, open PRs, print credentials or write to Doppler.

Do not mirror runtime secrets back into the GitHub Secrets UI; Doppler
is the authoritative configuration source. The GitHub repository secret
DOPPLER_TOKEN is the only necessary bootstrap exception.

## Repository-facing changes

Treat target repositories as independent software projects.

Use conventional, descriptive PR/commit titles based on the requested
change (examples: "test: cover canonical JSON edge cases", "fix: handle
empty search results"). Branches should describe the change and issue,
not the orchestration framework. Commit identity uses the selected
authorized GitHub account.

PR descriptions should state the change scope and validation details,
and link the target GitHub issue via "Closes #N" when appropriate.
Do not put orchestration implementation, run URLs, internal job IDs,
agent/model names, temporary infrastructure names, authentication
mechanisms, or fleet project names in target PRs, issues or comments.

Internal diagnostic records (worker run, artifact digest, selected profile,
scanners, failures and account) remain in the operator's private receipt
and the internal Multica issue. They do not belong in target repositories.

Existing records in the initial experimental pilot may preserve historical
information. New publications use this policy by default.
