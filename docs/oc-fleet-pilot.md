# Portable fleet MVP (2026-10-15)

This is a read-only OpenCode worker foundation shared by Multica frontier-manager tasks and model-free dispatchers. It **does not** yet automatically connect Multica issues to Actions, open target-repository PRs, repair failures, or merge.

- Trusted workflow: .github/workflows/oc-fleet-worker.yml, manually dispatched on protected main.
- Default mode: config-only, which only validates the non-secret manifest.
- Execution: one temporary GitHub-hosted OpenCode runner using a reviewed profile, allowlisted target, pinned checkout SHA and strict file scopes.
- Sources: frontier and rules are informational labels, not credentials. Dispatch authorization must be checked separately.
- Profiles: base, python-ruff, security-semgrep; only pinned tools in reviewed workflow code, no arbitrary plugin URL, MCP, CLI or secret from issue text.
- Execution uses read-only checkout, no persisted GitHub credentials. Evidence has run/attempt/task identity, immutable checkout SHA, patch digest and allowed paths. Artifact JSON is **not independently signed** and must not be used alone to authorize PR publication.
- Current mode deliberately rejects publish=true and phase=repair. PR publishing and review/CI repair need a separately approved privileged workflow and independent GitHub run verification.
- Scope: public test repositories only; default FLEET_ALLOWED_REPOS is oaslananka-dev/oc-test, otherwise comma-separated trusted GitHub Actions repo variable.
- Existing F1/F2 workflows and branch policy unchanged.

Config-only example (replace SHA with an approved real commit before live execution):

~~~json
{
  "schema_version": 1,
  "task_id": "pilot-001",
  "source": "frontier",
  "repository": "oaslananka-dev/oc-test",
  "checkout_sha": "2177832e1d0ea78aebc7354a438cd0e697498542",
  "base_branch": "main",
  "issue_key": "OCFI-42",
  "prompt": "Update a permitted sandbox fixture and write a regression test.",
  "model": "opencode/big-pickle",
  "profile": "base",
  "allowed_paths": ["playground/fleet/"],
  "phase": "implement",
  "publish": false
}
~~~

Switch source to rules for a model-free dispatcher. The same contract applies.

Acceptance before further expansion: green offline contract CI; successful manual config-only run; real execute run with native GitHub job evidence; verified scope and isolation; then separate privileged publisher design. A requested 20-run limit is not proof of actual model-provider capacity.
