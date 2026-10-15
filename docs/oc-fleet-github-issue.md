# One GitHub issue -> ephemeral worker -> PR (no frontier, no new App)

Operator host: amd2700x, signed-in GitHub CLI accounts, Python 3.13.
Runner: oaslananka-dev/oc-test GitHub Actions, OpenCode CLI in temporary Ubuntu.
The Windows control process is deterministic code; **no second LLM**.

1. Provide an existing GitHub issue number, target repository and allowed paths.
2. Controller checks the selected target GitHub identity has repo push permissions,
   reads the issue directly, binds an immutable default-branch SHA, and registers
   the target in runner repo FLEET_ALLOWED_REPOS (on live dispatch only).
3. The approved workflow checks out the target at that SHA. OpenCode generates a
   change in its ephemeral runner with no persisted GitHub credentials.
4. The controller captures the exact GitHub workflow_dispatch run ID (REST
   return_run_details=true), verifies workflow/repo identity, the two successful
   jobs, exact artifact ID/run/attempt, evidence SHA256 and allowed paths.
5. The operator PC (not OpenCode) clones the target repo using the selected
   existing gh identity, independently applies and verifies the exact patch,
   and opens a new fleet/task branch and GitHub PR. **No auto-merge or deploy.**

## Run commands
From:
C:\Users\Admin\Desktop\PROJECTS\multica-test\oc-test

PowerShell:

    python .github/scripts/oc_fleet_issue.py run --repo OWNER/REPO --issue 123 --path src/ --model opencode/big-pickle --profile base --worker-account oaslananka --target-account oaslananka --dry-run

Remove --dry-run to actually dispatch the worker, await completion, reverify
the patch and create a PR using the selected target identity.

For nondefault target identity (the login is already stored by gh CLI):

    python .github/scripts/oc_fleet_issue.py run --repo OWNER/REPO --issue 123 --path src/ --worker-account oaslananka --target-account MakerParsDev

If the shell loses connection after dispatch, resume instead of dispatching
another task:

    python .github/scripts/oc_fleet_issue.py resume --repo OWNER/REPO --issue 123 --target-account MakerParsDev --worker-account oaslananka

Resume reads a durable receipt under %LOCALAPPDATA%\ocfilo-fleet and rechecks
the exact run. It will refuse if the base SHA advanced. Preview changes by
using --verify-only (no PR).

## Supported and not yet supported
- Supported: open GitHub issue, PUBLIC target repository, reviewed source
  paths, worker/publisher identities selected from existing gh logins,
  models from the worker contract allowlist, base/python-ruff/security-semgrep,
  one idempotently dispatched issue, temporary worker, verified artifact, PR.
- Public cross-repository checkout works with read-only GITHUB_TOKEN.
- For PRIVATE cross-repository checkout, use a separate read-only token in the
  runner secret FLEET_CHECKOUT_TOKEN. Until this is configured the controller
  refuses private targets rather than copying a broad-scope personal token.
- Changes under .github, hidden directories, symlinks, deletions, arbitrary
  MCPs/plugins, remote CLI commands and arbitrary paid models are not allowed
  by the current safe pilot profile. Extend through reviewed profiles.
- PR CI/review observer and automated repair/merge remain later steps; PRs
  are never implicitly merged. Existing F1 tests are unchanged.
- A previous task receipt blocks accidental duplicate dispatch. The operator
  must explicitly reconcile a previous run before a new attempt.

No permanent extra Multica agent, Frontier, GitHub App, or second LLM is used.
Existing Multica issue pilot remains available independently.

## Terminal failure retry (at most three total attempts)
Only after GitHub verifies a terminal FAILED/CANCELLED/TIMED_OUT run, call run with --retry-failed and the original repo, issue and approved paths. The old native run/task identity is preserved in the receipt history. A successful/pending run cannot be duplicated. Scope gate diagnostics include only a bounded escaped filename, never model logs or file contents.
