# Repository documentation

Navigation for the documents tracked under `docs/`. These pages describe
separate components and historical acceptance stages; they do **not** imply
that every capability is enabled in production.

## F2 orchestration and run evidence

- [F2 planning and roadmap](ocfilo-f2.md)
- [F2 bounded runner pilot](ocfilo-f2-pilot.md)
- [F2 capacity measurements](ocfilo-f2-capacity.md)
- [F1 main branch protection history](ocfilo-main-protection-plan.md)

## Fleet configuration and integrations

- [Configuration and model policy](oc-fleet-configuration.md)
- [Multica task dispatch](oc-fleet-multica-dispatch.md)
- [Detached worker reconciliation](oc-fleet-multica-detached.md)
- [Multi-repository PR-only acceptance](oc-fleet-multica-multi-repo.md)
- [Manual external PR terminal reconciliation](oc-fleet-external-reconcile.md)
- [One-command Fleet intake and status](oc-fleet-submit.md)
- [Local Agent Fleet Control MCP](oc-fleet-control-mcp.md)
- [Specialist and tool profiles](oc-fleet-tool-profiles.md)
- [Temporary agent role selection](oc-fleet-agent-roles.md)
- [Nerlex integrations](oc-fleet-nerlex-integrations.md)
- [Multica telemetry](oc-fleet-multica-telemetry.md)

## Publication, review and security

- [GitHub issue task contract](oc-fleet-github-issue.md)
- [Trusted publisher](oc-fleet-publisher.md)
- [Initial worker pilot](oc-fleet-pilot.md)
- [CI review triage](oc-fleet-ci-review-triage.md)
- [Scoped production repair](oc-fleet-scoped-production-repair.md)
- [Automated Codacy repair](oc-fleet-automatic-codacy-repair.md)
- [OSV vulnerability discovery](oc-fleet-osv-discovery.md)
- [OSV npm lockfile validation](oc-fleet-osv-npm-lockfile.md)
- [OSV existing-PR publisher](oc-fleet-osv-existing-pr-publisher.md)

## F2 planning text provenance

The historical F2 planning file was committed with 169 Unicode replacement
characters (U+FFFD) and two control characters. No authoritative intact
original was found in the tracked history. The linked F2.1 document has
therefore been **editorially rewritten in Turkish**, not faithfully recovered
byte-for-byte. Its replacement text preserves verifiable F2.1 design
boundaries, points to independently documented later stages and requires
human content review under [issue #113](https://github.com/oaslananka-dev/oc-test/issues/113).
Do not interpret historical plan-only claims as current runtime defaults.
