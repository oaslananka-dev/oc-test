# Public GitHub issue and pull-request copy boundary

The controller source repository is itself **public**. Do not treat public
GitHub issues, pull requests, repository files or workflow artifacts as a
confidential evidence vault. Keep sensitive execution provenance in
authenticated runtime records and protected local operator logs instead of
copying it into public source-repository narratives. Public third-party
repositories such as ADXL355 should only contain project-relevant content.

For an external public repository:

- The issue title/body describe **only** a product defect, feature, affected
  source paths, reproducible steps, expected behavior, and acceptance tests.
  No account names, control-plane task IDs, models, paid/free providers,
  execution queues, tokens or internal URLs.
- The external issue-creation MCP tool and production dispatch admission
  reject internal identifiers in the proposed public copy.
- The trusted PR publisher emits a conventional change title (including
  scoped forms such as `fix(python): ...`), a concise change summary, changed
  paths, validation guidance and the public issue reference, without agent
  execution data.
- The publisher refuses known temporary internal acceptance directories such
  as `playground/fleet/` in public targets. Use a disposable dedicated
  acceptance repository instead of the product source repository.
- Review, security, Merge SHA, run IDs and model/account provenance remain
  available to authenticated operators through runtime status and local logs;
  they do not need to be copied into public PR comments.
- If a user directly creates an issue outside this controller, GitHub allows
  that public text regardless of our guard. The controller must not dispatch
  it as an external task until its text passes the public-copy admission.
- Do not rewrite protected Git history to hide old fixture paths or commit
  titles. A dedicated cleanup PR removes obsolete source-tree fixtures from
  current `main`, while old commit objects remain visible in history.

Negative-first tests cover public text denial, issue admission, trusted PR
publication filename restrictions, and conventional scoped titles.
