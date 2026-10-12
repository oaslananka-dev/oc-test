# OpenCode V2 Parametric Runner

This repository tests externally dispatched OpenCode V2 tasks on GitHub Actions.

## Use the Actions UI

Open **Actions → OpenCode V2 Parametric Runner → Run workflow**.

- `run_mode=config-only`: installs OpenCode and checks configuration without running an LLM task or needing an API key.
- `run_mode=execute`: calls the selected provider and executes the specified task; requires provider credentials as GitHub Actions secrets.
- `model`: e.g. `anthropic/claude-sonnet-4-5`.
- `agent`: `ci-review`, `ci-implement`, `build`, or a custom agent defined in `config_json`.
- `config_json`: additional OpenCode V2 JSON for agent definitions, skills, plugins, MCP servers and policies. Trusted sources only.

## External trigger (GitHub CLI)

```bash
gh workflow run oc.yml --repo oaslananka-dev/oc-test --ref main \
  -f run_mode=config-only \
  -f model=anthropic/claude-sonnet-4-5 \
  -f agent=ci-review \
  -f task='Inspect repo structure' \
  -f config_json='{}'
gh run list --repo oaslananka-dev/oc-test --workflow oc.yml
```

For real model execution, configure the matching GitHub Actions secret (e.g. `ANTHROPIC_API_KEY`), then change `run_mode` to `execute` and task to:
`Use runner-check to list repo files and explain how the skill was loaded.`

To test local code edits, choose `ci-implement` and send:
`Create hello.txt containing OpenCode works and describe the diff. Never push.`
The runner does **not** push or merge. Changes and stdout are uploaded as an Actions artifact.

### Sample config_json

```json
{
  "agents": {
    "tester": {
      "mode": "primary",
      "system": "Check the supplied integrations and report results."
    }
  },
  "skills": ["./.opencode/skills"],
  "plugins": ["TRUSTED_PLUGIN@1.2.3"],
  "mcp": {
    "servers": {
      "docs": {
        "type": "remote",
        "url": "https://YOUR-MCP-ENDPOINT/mcp",
        "oauth": false
      }
    }
  }
}
```

This is illustrative only: replace the plugin and endpoint with trusted sources and select `agent=tester`. Do not place secrets in inputs. Use GitHub Actions secrets for provider keys. `config-only` does not prove that remote servers or models are reachable. Skills/plugins/MCP may execute third-party code; this workflow must be dispatched only by authorized parties.

Official docs: https://opencode.ai/v2/docs/
