# OpenCode V2 — free-model GitHub Actions runner

Runs OpenCode on Ubuntu through externally supplied GitHub Actions parameters. Default: **`opencode/big-pickle`**, **`build`**, **`execute`**. No provider API keys are passed to the workflow.

## First test

Open [Actions → OpenCode V2 Parametric Runner](https://github.com/oaslananka-dev/oc-test/actions/workflows/oc.yml), select **Run workflow**, keep `model=opencode/big-pickle`, `agent=build`, `run_mode=execute`, and enter a harmless prompt such as:

`Read README.md and report its title. Do not edit any files.`

Or trigger externally:

```bash
gh workflow run oc.yml -R oaslananka-dev/oc-test --ref main \
  -f model=opencode/big-pickle \
  -f agent=build \
  -f run_mode=execute \
  -f task='Read README.md and report its title. Do not edit any files.' \
  -f config_json='{}'
```

`config-only` validates configuration without querying a model. `execute` contacts OpenCode Console and **may fail without authentication**, even if the listed model is free. An account login / free model access policy is distinct from API pricing. The runner never switches to a paid model. Do not add a paid-provider API key as a workaround.

## Parameters

- `task`: untrusted task text (never include secrets)
- `model`: exact zero-price OpenCode Console model ID, e.g. `opencode/big-pickle`, `opencode/nemotron-3-ultra-free`, `opencode/ling-3.1-flash-free`
- `agent`: built-in `build` or `plan`; custom agents must be defined in `config_json.agents`
- `run_mode`: `config-only` / `execute`
- `output_format`: `default` / `json` (OpenCode v2 CLI accepts `default` instead of `text`)
- `opencode_version`: pinned stable v2 release
- `config_json`: trusted JSON configuration for agents, skills, plugins, MCP and permissions

Example custom agent overlay:

```json
{
  "agents": {
    "tester": {
      "mode": "primary",
      "system": "Inspect the checkout, run a minimal test, and report the result. Never push."
    }
  },
  "skills": ["./.opencode/skills"]
}
```

Set `agent=tester`. The example skill is in `.opencode/skills/runner-check/SKILL.md`.

Results: Actions logs and `opencode-v2-<run_id>` artifact. The checkout token is read-only and not persisted; this is an ephemeral sandbox, with no automatic push or merge. However, an authorized dispatcher can supply external plugins/MCP endpoints that execute code, so do not expose workflow dispatch to untrusted parties.

Reference: https://opencode.ai/v2/docs/console/models/ and https://opencode.ai/v2/docs/
