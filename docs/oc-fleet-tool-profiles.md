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
The live Doppler catalog (20 October 2026) contains **seven** reviewed
profiles:

| Named tool profile | Specialty |
| --- | --- |
| `python-docs` | Python/docs with pinned CLI and plugin |
| `context7-mcp` | Documentation MCP integration |
| `python-backend` | Backend correctness and tests (skill-only) |
| `embedded-firmware` | Hardware-aware embedded C/C++ review (skill-only) |
| `security-review` | Security analysis and negative tests (skill-only) |
| `frontend-ux` | Frontend UX/accessibility checks (skill-only) |
| `ci-maintenance` | CI root-cause and regression handling (skill-only) |

Five new presets were validated against the original Doppler catalog without
adding packages, credentials, model providers or paid APIs. Two controller
`--dry-run` checks against `oaslananka/adxl355#130` succeeded using
`python-backend` and `embedded-firmware`. This proves catalog resolution
and safe task planning, **not** a new end-to-end model/skill execution.
Unknown names require an explicitly reviewed catalog entry and fail a
controller dry run before any GitHub worker launches.
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

## Live named-skill acceptance: ADXL355 OAS-27 (20 October 2026)

The first **real** GitHub-hosted worker using the newly configured
python-backend specialist completed successfully, not merely a dry run.

- [GitHub issue #132](https://github.com/oaslananka/adxl355/issues/132)
  constrained the task to a smbus2 adapter fix and a new fake-bus test file.
- Multica OAS-27 selected free model opencode/big-pickle, execution profile
  python-ruff, specialist python-backend, and no paid integrations.
- [GitHub worker run #39473156707](https://github.com/MakerParsDev/oc-test/actions/runs/39473156707)
  finished SUCCESS in one attempt. Task contract, named tool-profile setup,
  actual OpenCode execution, scoped evidence creation and artifact upload
  all completed successfully. Original artifact:
  fleet-39473156707-1-gh-132-62d84e6dbd13.
- The scheduled detached controller reconciled the immutable artifact and
  opened [ADXL355 PR #133](https://github.com/oaslananka/adxl355/pull/133)
  with exactly python/src/adxl355/adapters/smbus2.py and
  python/tests/test_smbus2_adapter.py. The original SPI PR #131 remained
  untouched. OAS-27 moved to in_review / pr_created.
- The original PR head 34af62394cef4954fcc083810a03969d5e608046
  independently passed 194/194 Python tests and 16/16 focused I2C/transport
  tests. Python 3.10, 3.11 and 3.12 CI checks passed. Codacy reported zero
  new issues. Consult the live PR before claiming other CI is green.
- **External PR-only stays enforced:** neither PR #133 nor issue #132 was
  automatically merged/closed. No new worker account, paid provider, checkout
  privilege, CI bypass or additional capacity was needed.

This proves real named-profile selection **and** GitHub-hosted agent execution
for python-backend. It does not independently certify every specialist
profile, concurrency above four, private repository checkout or automatic
cross-account routing.

## Two-specialist concurrent acceptance (20 October 2026)

The deployed four-worker system independently dispatched **two different
named skill profiles in the same 21:40 systemd tick**, without a chat-managed
model or additional worker account:

| Multica | Specialist / execution | Target issue | Worker run | External PR |
| --- | --- | --- | --- | --- |
| OAS-28 | embedded-firmware / base | [ADXL355 #134](https://github.com/oaslananka/adxl355/issues/134) | [39476805038](https://github.com/MakerParsDev/oc-test/actions/runs/39476805038) | [#136](https://github.com/oaslananka/adxl355/pull/136) |
| OAS-29 | security-review / python-ruff | [ADXL355 #135](https://github.com/oaslananka/adxl355/issues/135) | [39476804362](https://github.com/MakerParsDev/oc-test/actions/runs/39476804362) | [#137](https://github.com/oaslananka/adxl355/pull/137) |

Both worker runs used the free opencode/big-pickle model, passed their
GitHub-hosted validation and actual unprivileged agent execution on attempt
1, and emitted independent authenticated artifacts. The scheduled detached
controller published each task as a scope-checked PR. Both OAS-28/OAS-29
entered in_review/pr_created. Neither production PR was auto-merged.

- Embedded C/SPI repair changes only c/examples/linux_spi_transfer.c and
  c/tests/test_linux_spi_transfer.c. Independent CMake
  ADXL355_BUILD_TESTS=ON and focused ctest succeeded.
  A follow-on test-only review commit replaced an analyzer-flagged buffer
  sentinel with a safe C struct assignment; Codacy then passed.
- Defensive calibration input validation changes only
  python/src/adxl355/calibration.py and python/tests/test_calibration.py.
  Independent test results: 29/29 focused calibration and 195/195 Python
  suite, with no hardware or exploit claims.
- Shared dependency-audit failures on both PRs were traced to the main
  branch Node lockfile's transitive vite 8.3.0 dependency, flagged by OSV
  GHSA-698v-4r3x-3vcv. The patched version is 8.3.3. The separate
  [dependency issue #138](https://github.com/oaslananka/adxl355/issues/138)
  and Multica OAS-30 track remediation. No OSV/Codacy check was bypassed.

This verifies **two concurrent distinct specialist skills**, not
automatic cross-account load balancing, private repository support, or
higher-than-four concurrency. Production PRs remain PR-only until their
individual checks and separate review/merge authority are satisfied.
