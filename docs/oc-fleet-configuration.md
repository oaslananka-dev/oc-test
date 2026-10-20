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

## Git commit identity and squash merge policy

Every published commit must use the selected signed-in GitHub user's
authenticated profile. Derive display name and numeric-ID noreply email
from gh api user, using the selected account token. Reject App/bot
identities, missing user IDs, fabricated author/committer metadata and
automation co-author trailers. Verify the actual commit metadata and
message independently before pushing.

For target repositories with squash merge enabled, require:
- squash_merge_commit_title = PR_TITLE
- squash_merge_commit_message = BLANK

These prevent GitHub from including intermediate commit subjects and
co-author trailers by default. New target repositories are checked before
dispatch; the controller does not silently alter their settings. A repo
administrator can configure the settings once under GitHub Settings,
General, Pull Requests.

For programmatic squash merges pass an explicit clean commit title and
message rather than accepting GitHub's generated history. These defaults
do not prevent a user from manually editing a message in the GitHub UI.

The legacy F2 local integrator also uses the configured Git author rather
than a fabricated integration persona. The older App-token publisher
refuses real writes if it cannot verify an authenticated human identity.

Already merged commits are NOT rewritten to remove historic attribution.
Force-pushing protected history would change SHAs and invalidate existing
references and verification signatures.


## Portable worker repositories (optional)

The original `oaslananka-dev/oc-test` remains the default worker and needs
no routing migration. For each additional public GitHub-account worker:

1. Install the reviewed worker workflow and its trusted scripts on that
   repository's protected `main` branch.
2. Set its repository Actions variable `FLEET_WORKER_REPOSITORY` to its exact
   `owner/repo`. Without this setting, a copied workflow refuses to run.
3. Install the read-only `DOPPLER_TOKEN` bootstrap secret for the `oc-test/main`
   configuration. Never expose it to the coding model.
4. Add the exact public repository name to `FLEET_WORKER_REPOS` in Doppler,
   and use `--worker-repo owner/repo --worker-account approved-user` on the
   trusted dispatcher. The selected account must have write access to trigger
   that repository's Actions workflow.
5. Keep `--target-account` independently scoped to the target GitHub project.
   The unprivileged worker never receives the target publication token.

The dispatcher verifies the source repository and active workflow before
dispatch, and again checks the immutable native run/artifact provenance before
publication. One target issue keeps a single receipt across worker repositories;
changing its worker requires explicitly reconciling the existing run first.
Worker repositories are *not* created or enabled by adding them to Doppler.


## External project PR-only compatibility

The controller normally requires the central pilot repo's squash defaults
`PR_TITLE/BLANK`. Active external projects may use
`COMMIT_OR_PR_TITLE/COMMIT_MESSAGES`; operators can explicitly select
`--external-pr-only` for a single approved public target GitHub issue.
This verifies **those exact defaults** rather than changing repository admin
settings or disabling other gates. It authorizes a scoped PR **only**:
target repo CI/review, merge method, original maintainer policy and release
controls remain external. Never infer permission for automated merges from
this option. No free/paid model selection changes.
