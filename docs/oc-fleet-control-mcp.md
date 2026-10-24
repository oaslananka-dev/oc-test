# Local Agent Fleet Control MCP

This is an **operator-side** MCP adapter to the existing AMD2700X Fleet
controller. It is NOT a second AI agent, dispatcher, queue, worker, webhook
receiver or HTTP service. The existing Multica projects are the canonical
task state. GitHub-hosted Actions + free OpenCode stay the sole code workers.

## How to connect on AMD2700X

Install once after a protected PR merge:

```sh
ln -s /home/amd/.local/share/oc-fleet-dispatch/bin/oc-fleet-mcp \
  /home/amd/.local/bin/oc-fleet-mcp
```

Python requires the existing, pinned **`mcp==2.1.1`** SDK on the **operator
computer**. The GitHub Actions worker does not load this SDK. The controller
currently uses Python 3.12.

Use the local stdio entrypoint with any MCP-capable agent. OpenCode v2:

```json
{
  "mcp": {
    "servers": {
      "oc-fleet-control": {
        "type": "local",
        "command": ["/home/amd/.local/bin/oc-fleet-mcp"]
      }
    }
  }
}
```

On an authenticated second device, use **SSH stdio**, with the owner’s
existing SSH/Tailscale identity and **no public TCP port or bearer key**:

```json
{
  "mcp": {
    "servers": {
      "oc-fleet-control": {
        "type": "local",
        "command": [
          "ssh", "-T", "amd@amd2700x",
          "/home/amd/.local/bin/oc-fleet-mcp"
        ]
      }
    }
  }
}
```

Adapt host/user only to actual verified SSH identity. A connected agent gains
the Fleet operator privileges of the remote Unix account, so do not put this
MCP on untrusted shared devices or expose unauthenticated tunnels. No
credentials belong in agent prompts or source control.

For Codex CLI with a verified SSH connection:

```sh
codex mcp add oc-fleet-control -- ssh -T amd@amd2700x \
  /home/amd/.local/bin/oc-fleet-mcp
```

## Available operations

- **Observe:** `fleet_health`, `fleet_list`, `fleet_status`,
  `fleet_run`, `fleet_logs`, `fleet_telemetry`, `fleet_pr`,
  `fleet_checks`.
- **Discover/prepare:** `fleet_open_issues` and `fleet_create_issue`
  (only existing owner-approved repositories), `fleet_plan`
  (real read-only admission).
- **Queue:** `fleet_submit` (exact target confirmation), then the original
  five-minute dispatcher runs the work; the MCP never directly starts a
  paid model, local worker or autonomous agent.
- **Before dispatch:** `fleet_pause_queued`,
  `fleet_resume_queued`, `fleet_cancel_queued`. These require the task's
  exact identifier, the existing dispatcher file lock, no worker receipt or
  claim, and truthful Multica readback. Paused tasks are only resumed after
  a fresh production admission check.
- **After PR:** `fleet_merge_reviewed_pr` only when the caller explicitly
  supplies the immutable HEAD SHA and confirms review, on an eligible
  trusted worker PR with permitted changed files and completed normal
  GitHub checks; **no `--admin`, branch-rule bypass or auto-merge**.
  `fleet_finalize_reviewed_merge` and `fleet_supersede_reviewed` delegate
  to the existing independent strict terminal reconcilers, default dry-run.

Only two installed Multica projects are accepted: main Fleet project
`0506e0e5-01a7-4de9-b63b-67e993809721` and the Fovux project
`9d64209f-2ba7-441b-a204-d5fd7bf0f962`. Automatic issue intake
`fleet_submit` continues through the existing main project and verified
allowlists. A task in the second project can be observed/reconciled, but
cannot be submitted through the main project's intake by silently changing
its project ID.

**Deliberate boundary:** stopping an already-running authenticated worker,
arbitrary workflow reruns, unrelated GitHub PR merging and the special
`oc-test` pilot auto-finalizer are NOT exposed by this v1. The existing
cancel admission module is offline-only; connecting an unchecked live
cancel endpoint would bypass its deliberately unimplemented approval
boundary. Do not claim these operations work or replace them with arbitrary
shell execution. Future cancel/retry tooling must preserve exact task/run
receipt verification, GitHub authentication, bounded attempts and audit.
The user can still perform unrelated administration through the separate
trusted Ops connector if appropriate; this narrowly defined Fleet MCP is
not a general-purpose system shell.

## Operator example

"Use oc-fleet-control to list available issues in an approved repository;
propose one issue with safe paths, plan it, and submit it only if the exact
target is authorized. Track the returned Multica task ID and linked free
worker run. Review the resulting PR/CI. Do not merge unless separately
directed to merge the exact SHA."

The MCP SDK protocol handshake, tool listing, OAS-40 read, OAS-39 cancelled
read, timer health, PR and CI check reads can be exercised through a real
stdio ClientSession without creating any task. Native GitHub worker jobs
never run on the operator PC.

## Deployment checks

- Run the Python Fleet and docs regression suite plus a real MCP
  `initialize` / `tools/list` / `tools/call` smoke test.
- Assert no duplicate task, missing permission bypass, unrelated repo,
  hidden path, arbitrary run ID or unsupported model authorization.
- Protected merge and exact-main GitHub CI must pass; fast-forward the
  controller only under `~/.local/state/oc-fleet/dispatch.lock`.
- Keep existing timer, Multica and free-model configurations unchanged.
- Avoid adding the same MCP server to multiple profiles unnecessarily;
  18 tools may use context tokens and there is no need for paid providers.
