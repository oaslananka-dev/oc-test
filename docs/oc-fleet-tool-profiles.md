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
