# One-command Fleet intake: `oc-fleet`

**Operator entrypoint, not another worker or service.** This CLI lives in the
existing trusted `oaslananka-dev/oc-test` controller checkout on AMD2700X.
Run it through Ops from any ChatGPT session/device, or from a terminal. The
Multica project is the single source of truth for assignments and progress;
the AMD2700X detached controller alone triggers GitHub-hosted OpenCode Actions.

## Typical request

Tell the Ops-connected assistant:

> Give `oaslananka/fovux-kit` issue `#137` to the free OpenCode Fleet.
> Limit edits to `fovux-mcp/pyproject.toml`; use security-review; prepare a PR,
> never merge automatically.

First **preview without writing**:

```sh
oc-fleet plan --repo oaslananka/fovux-kit --issue 137 \
  --path fovux-mcp/pyproject.toml \
  --agent security-review --profile security-semgrep
```

The preview reuses the **existing production worker dry-run**: GitHub
repository/issue identity and open status, active worker permissions, selected
target account, Doppler repository allowlist, current default branch SHA,
approved model/profile/agent and normalized scope are all checked.
It does not implicitly whitelist a target, create a task, dispatch a worker,
or merge anything. If the repository is not already authorized, explicitly
review and authorize it using the existing operator process first. Do not
auto-add arbitrary repositories to the allowlist.

After reviewing the exact repository, issue and file scopes, explicitly
authorize one enqueue operation:

```sh
oc-fleet submit --repo oaslananka/fovux-kit --issue 137 \
  --path fovux-mcp/pyproject.toml \
  --agent security-review --profile security-semgrep \
  --confirm-target oaslananka/fovux-kit#137
```

**This is an example, not an already-submitted task.** `submit` takes an
exclusive lock shared with the production dispatcher, re-verifies live preflight,
and checks the complete project task inventory for duplicates. It creates a
Multica `todo` issue, writes bounded routing metadata with no implicit
authorization, stages `fleet_ready=false`, checks the exact resulting record
against the established Multica contract, then writes **`fleet_ready=true`
last**. If a write fails before the final step, the issue remains unready and
must be manually inspected; do not blindly resubmit. The `queued` response
returns the one canonical Multica task ID.

The five-minute controller admits a task only if worker capacity, live
GitHub checks, no conflicting PR scope, safe source checkout, token identity
and other existing constraints allow it. **Queued is not dispatched.** It
does not guarantee when a PR will be ready.

## Status and results

```sh
oc-fleet status OAS-123
oc-fleet list
```

Both commands are **read-only**. They report the Multica issue's state,
repo/issue binding, active worker run, created PR, result, merge SHA and
supersession reference when known. Missing fields remain `null`; do not
interpret a queued task or a PR as a merged or completed outcome.

GitHub PR code review/merge are separate, protected processes. Only verified
authentic external worker merge evidence can mark `done` through the
existing external finalizer. Separately superseded, unmerged worker PRs
may be operator-cancelled only via the independent explicit reconciliation
mechanism. Nothing in `oc-fleet submit` directly changes those policies.

## Limits and supported options

- Public repositories already approved in `FLEET_ALLOWED_REPOS`, with a
  matching authenticated target account and authorized worker
  `MakerParsDev/oc-test`. No new credentials, agent products or local/paid
  models.
- A pre-existing **open GitHub issue** is required. Task instructions come
  from the issue body, not arbitrary shell text. Amend the issue first.
- `--path` must be a safe explicit source file/directory, repeatable up to
  12 non-overlapping entries. Hidden files, `.github`, traversal, arbitrary
  shell execution and unapproved tool profiles are not admitted.
- Default free model: `opencode/big-pickle`. The worker account and repo
  are chosen from the existing Doppler-approved, verified multi-account pool
  and written to each Multica task; see
  [multi-account worker pool](oc-fleet-worker-pool.md).
  Alternative models/agents must be allowed by the existing contract.
  Production capacity stays four.
- Pilot self-repository requests, external auto-merge, private targets and
  unapproved repos fail closed. Higher concurrency remains out of scope.
- No background daemon or extra timer is introduced by this CLI.
- To run without adding an alias:
  `python3 -B .github/scripts/oc_fleet_submit.py plan --help`
- On the operator device, link `bin/oc-fleet` to an existing PATH directory
  only after protected merge and fresh post-merge tests; it is a wrapper
  around the current deployed trusted controller script.

## Read-only smoke test

```sh
oc-fleet status OAS-31
oc-fleet list
```

OAS-31 should report a verified `done`; separate superseded/cancelled tasks
must never be counted as a successfully merged worker PR.
