# Multica → temporary OpenCode agent selection

Set optional `fleet_agent` on a Multica task:

| Value | Behavior |
| --- | --- |
| `build` (default) | Existing OpenCode build agent |
| `python-backend` | Python/backend specialist |
| `embedded-firmware` | Embedded C/C++/hardware specialist |
| `security-review` | Security remediation specialist |

The agent is independent of `fleet_model`, `fleet_profile`
(`base`, `python-ruff`, `security-semgrep`) and reviewed
`fleet_tool_profile` (Doppler MCP/skills/CLI bundle).

An approved task requires `fleet_ready=true`, an authorized target,
exact GitHub issue/path scope and trusted accounts. The existing
five-minute controller timer dispatches temporary worker jobs within its
current four-worker ceiling. No paid model, additional workers, auto merge
or native Multica Autopilot is enabled.

Existing tasks and receipts without `fleet_agent` stay mapped to
`build`. Changing `fleet_agent` after dispatch causes reconciliation
to reject a mismatched immutable receipt, not modify a running agent.

This feature requires synchronized worker repo contract/workflow and
controller rollout.
