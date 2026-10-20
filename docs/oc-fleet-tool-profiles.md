# Task-selectable OpenCode tools

The tool catalog is stored in Doppler project oc-test, config main, with
key FLEET_TOOL_PROFILES. Runtime secrets remain in Doppler; GitHub Actions
keeps only the read-only DOPPLER_TOKEN bootstrap secret.

The catalog is a JSON object with version 1 and a profiles map.
Each named profile can contain:

- plugins: version-pinned npm OpenCode plugin packages
- mcp: remote public-HTTPS servers, or local argv-based servers
- skills: Markdown SKILL.md content and descriptions
- cli: version-pinned npm and pipx packages
- secrets: selected Doppler secret key names (never secret values)

Example:

    {
      "version": 1,
      "profiles": {
        "python-docs": {
          "plugins": ["@upstash/context7-opencode@0.2.1"],
          "mcp": {
            "reference-docs": {
              "type": "remote",
              "url": "https://mcp.context7.com/mcp",
              "oauth": false,
              "headers": {"CONTEXT7_API_KEY": "{env:CONTEXT7_API_KEY}"}
            }
          },
          "skills": [
            {
              "name": "python-review",
              "description": "Test-first changes for Python repositories",
              "content": "Write deterministic unit tests before production changes."
            }
          ],
          "cli": {
            "pipx": ["ruff==0.13.1"],
            "npm": ["@codacy/analysis-cli@0.24.0"]
          },
          "secrets": ["CONTEXT7_API_KEY"]
        }
      }
    }

An operator can add or edit profiles directly in Doppler, without changing
the GitHub worker source. The task simply selects a different profile name.

Example operator command:

    python .github/scripts/oc_fleet_issue.py run --repo OWNER/REPO --issue 123 --path tests/ --tool-profile python-docs --worker-account oaslananka --target-account oaslananka --dry-run

Remove --dry-run to dispatch. The temporary worker uses only selected,
version-pinned CLI packages, OpenCode v2 plugins/MCP/skill configuration,
and only the explicitly declared Doppler secret keys. It never receives
the Doppler service token or a GitHub write token. The normal read-only
worker artifact and trusted PR publication gates remain in place.

Old fixed --integration choices remain compatible but are separate from
the flexible --tool-profile mechanism.

This is an operator-controlled extension catalog, not a way for issue
text or untrusted source files to inject commands. New third-party
plugins, MCP servers and CLI packages execute code: review their source,
pin versions, restrict credentials and test on a disposable task.


## Select an expert profile from a Multica task

The Multica dispatcher now forwards reviewed selections from each issue's
metadata rather than forcing every task to `base`. This is a **selection
mechanism**, not a new general-purpose model or arbitrary plugin installer.

| Multica metadata | Example value | Meaning |
| --- | --- | --- |
| `fleet_model` | `opencode/nemotron-3-ultra-free` | Exact free model ID from the worker allowlist |
| `fleet_profile` | `python-ruff` | Existing execution profile: `base`, `python-ruff`, `security-semgrep` |
| `fleet_tool_profile` | `python-docs` | Named, operator-reviewed Doppler `FLEET_TOOL_PROFILES` entry |
| `fleet_integrations` | `'["context7","codacy"]'` | JSON-encoded allowlisted integration names |

If these four fields are absent, existing tasks behave unchanged:
`opencode/big-pickle`, `base`, no additional tool profile, no integration.
The **already configured** catalog has `python-docs` and `context7-mcp`.
Other names require an explicitly reviewed catalog entry; unknown names
fail the controller's `--dry-run` before any GitHub worker launches.
The catalog can contain task-oriented SKILL.md guidance (Python tests,
embedded review, UI review), version-pinned CLIs, plugins, and MCP servers.
A skill is guidance, not a second agent or a separate model provider.

Multica CLI stores arrays as strings. For example:

```bash
multica issue metadata set OAS-XX --key fleet_model --value opencode/big-pickle --type string
multica issue metadata set OAS-XX --key fleet_profile --value python-ruff --type string
multica issue metadata set OAS-XX --key fleet_tool_profile --value python-docs --type string
multica issue metadata set OAS-XX --key fleet_integrations --value '["context7"]' --type string
```

Substitute an existing, unready task identifier for `OAS-XX`, then run
`oc_fleet_multica.py --mode plan` for a verified dry run before setting
`fleet_ready=true`. Never change routing/profile selections during an
in-progress worker: detached reconciliation checks the original durable
manifest against every selected profile field and refuses changed input.
A non-pilot repository still requires `fleet_external_pr_only=true`
and cannot request auto-merge. The systemd limit remains four workers.

The worker only accepts explicitly listed free-model IDs. An unavailable
free model fails the worker: it does not fall back to any paid model, start
another worker, or create a PR from failed evidence.
