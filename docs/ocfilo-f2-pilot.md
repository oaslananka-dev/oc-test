# ocfilo F2.2 - Bounded manual OpenCode pilot

**Scope:** This is a limited proving ground, NOT a universal 10-agent executor.
No native Multica Autopilot or webhook is created. Existing F1 workflow,
observer, repair queue, publisher checks and protection rules remain unchanged.

## Exact runner design

- Workflow: `.github/workflows/ocfilo-f2-worker-pilot.yml`.
- **Only explicit workflow_dispatch on protected main.** One planning job
  validates GitHub repository name and numeric ID, Git ref, event type,
  checkout SHA, run ID and run attempt; it selects exactly 1, 2 or 3 reviewed
  task IDs. No schedule, push trigger, webhook or bridge.
- Each selected task gets **its own independent Ubuntu 24.04 matrix runner**,
  not a shared mutable worktree. Max parallel: 3.
- Task/issue/identity contract is static and reviewed in
  `examples/ocfilo-f2-execution-pilot.json`: three independent tasks,
  issue #10, `opencode/big-pickle`, `build`, `actions-readonly`, one exact
  file under `playground/f2/` per task. No custom MCP/plugin/credential.
- `run_mode=config-only` (default): validate, create config and produce
  metadata. **No model call**. `run_mode=execute`: install pinned
  `@opencode/cli@2.0.27` and run one assigned OpenCode prompt per runner.
  No paid model/API token fallback if provider denies access or rate limits.
- `contents: read`, checkout `persist-credentials: false`,
  no `GH_TOKEN` / PAT / deployment secrets provided to OpenCode. OpenCode
  itself executes inside an ephemeral GitHub-hosted runner: this is bounded
  CI execution, not a cryptographic isolation boundary.
- Result gate validates exact SHA/run attempt/owner, unchanged trusted config,
  Git worktree changes limited to **only the one assigned regular file**,
  bounded file and patch size, non-binary patch, git diff --check and agent
  success. Any failure rejects publication.
- Artifacts contain `result.json` with repo ID, issue, task, run/attempt,
  SHA, outcome and ownership; successful execution also provides a scoped
  `changes.patch`. Raw model output logs are NOT uploaded. Artifacts are
  authenticated via native GitHub Actions run/attempt provenance when
  consumed; they are not independently signed.
- The workflow **never** pushes, opens a PR, merges, deploys, launches
  another workflow, starts Multica or updates external systems.

## F2.3a - Deadline, failure taxonomy and evidence contract

Still 1-3 workers. Capacity above 3 is NOT validated by this change.

- **Hard per-agent deadline.** `agent_deadline_seconds` (300/600/900, default
  600) is validated in the planning job and enforced on the runner with
  `timeout --signal=TERM --kill-after=20`. A step-level `timeout-minutes: 16`
  is only a backstop so the evidence step always runs.
- **Failure classes** in `result.json` (`failure_class`): `TIMEOUT`,
  `RATE_LIMITED`, `QUOTA_EXHAUSTED`, `PROVIDER_ERROR`, `NETWORK`,
  `AGENT_ERROR`, `NOT_STARTED` (agent step skipped, no model call),
  `CANCELLED`, `NO_CHANGE`, `SCOPE_VIOLATION`, `CONFIG_TAMPERED`,
  `OUTPUT_INVALID`, `EVIDENCE_INVALID`. Provider classes come from a pattern
  match on the last 64 KiB of the agent log inside the runner; only the class
  is uploaded, never log text. The class is advisory telemetry from an
  untrusted log and never grants authority.
- **Terminal states** (`worker_outcome`): `SUCCEEDED`, `FAILED`, `CANCELLED`,
  `CONFIG_ONLY`. `PENDING`/`READY`/`RUNNING` exist only as live GitHub job
  states; `BLOCKED` arrives with the DAG runtime.
- **Retry is advisory.** `retryable` is true only for `TIMEOUT`,
  `RATE_LIMITED`, `PROVIDER_ERROR`, `NETWORK`. Nothing retries automatically.
  A human re-runs failed jobs; there is no silent paid-model fallback.
  `QUOTA_EXHAUSTED` is never retryable.
- **Idempotency.** `idempotency_key` = sha256(repo ID, checkout SHA, task ID).
  It is identical across run attempts, so a consumer can detect a task that
  already `SUCCEEDED` on that SHA. Run ID and attempt identify each execution.
- **Measurement.** `agent_exit_code`, `agent_seconds`, `deadline_seconds` are
  recorded for capacity planning (needed before 5 and 10 workers).
- **Artifact name** is `ocfilo-f2-<run_id>-<run_attempt>-<task_id>`, so a
  re-run attempt cannot collide with an earlier artifact. A missing artifact
  now fails the job instead of warning.
- `validate_evidence()` in `ocfilo_f2_pilot.py` is the consumer contract
  (schema version 2). `finalize` validates its own output with it.

Not yet built: lease/heartbeat, automatic bounded retry runner, DAG wave
runtime, 5/10 worker capacity runs, result integration (F2.4).

## F2.3b - Offline DAG scheduler (proposal only)

`.github/scripts/ocfilo_f2_sched.py` decides what could run next. It reads a
plan, schema-2 evidence for one exact checkout SHA and the task IDs GitHub
reports as running. It starts nothing, calls no API and authorizes nothing
(`proposal_only: true`). A human-initiated workflow must act on the proposal.

States per task: `SUCCEEDED`, `RUNNING`, `READY`, `PENDING`, `FAILED`,
`CANCELLED`, `BLOCKED`.

- `READY`: all dependencies `SUCCEEDED`, task not running, not finished,
  retry budget left. `PENDING`: a dependency is not done yet.
- `BLOCKED`: a dependency is `FAILED`, `CANCELLED` or `BLOCKED` (transitive).
  Independent branches keep going; one failure never discards other results.
- **Retry:** only when the last failure class is advisory-retryable and fewer
  than `max_attempts` (default 2, max 3) distinct runs failed. `QUOTA_EXHAUSTED`,
  `SCOPE_VIOLATION` and `CANCELLED` are terminal. `CONFIG_ONLY` evidence is not
  an attempt.
- **No double start:** a `SUCCEEDED` task is never proposed again; a `RUNNING`
  task is never proposed and uses one capacity slot.
- **Capacity:** at most `VALIDATED_CAPACITY` (3) minus running tasks. Raise it
  only after a measured 5-worker run.
- **Fail closed:** evidence for another SHA, unknown task, wrong path/model/
  identity/issue, tampered fields, conflicting duplicates, success recorded
  before its dependencies, or an unknown or premature `running` ID rejects the
  whole step. Exact duplicate evidence is idempotent. Result is independent of
  evidence order.
- Outcome: `COMPLETE`, `IN_PROGRESS`, or `HALTED` (nothing ready or running,
  work remains).

Local use:
`python3 .github/scripts/ocfilo_f2_sched.py --plan examples/ocfilo-f2-ten-task-plan.json --evidence-dir <downloaded artifacts> --sha <40-hex> --output out.json`
(output file is never overwritten).

## F2.3c (part 1) - Manifest-driven DAG worker glue

`.github/scripts/ocfilo_f2_dag.py` lets a trusted, human-started workflow run a
reviewed DAG in rounds. This part adds the offline logic only; the workflow
that calls it is a separate change.

- Only manifests registered in `DAG_PLANS` (currently `dag-pilot`,
  `examples/ocfilo-f2-dag-pilot.json`: audit, then backend and frontend in
  parallel, then report) are accepted. Constraints on every task: issue 10,
  `opencode/big-pickle`, `build`, `base`, `actions-readonly`, exactly one
  writable file under `playground/f2/dag/`.
- `matrix`: asks the F2.3b scheduler what may run this round (capacity at most
  3) and emits `task_id`, `allowed_path`, `deadline_seconds`, `round`. No prompt
  text in the matrix. Evidence from the current or a later round is rejected.
- `prepare` / `finalize` reuse the F2.2 scope, config and evidence rules through
  the manifest instead of the fixed 3-task table. Evidence now carries `round`
  (1..6) so a retry in a later round of the same run is a distinct attempt.
- `state --require-complete`: records the final scheduler state and fails
  unless every task `SUCCEEDED`.
- Dependencies gate ordering only. A downstream worker does not receive its
  dependencies' patches; combining results is F2.4.

## F2.3c (part 2) - Manual DAG run workflow

`ocfilo F2 Bounded Manual DAG Run` (`ocfilo-f2-dag-run.yml`) calls the reusable
`ocfilo-f2-dag-round.yml` for rounds 1 to 6 in sequence.

- Trigger: `workflow_dispatch` on `main` only. Inputs: `plan` (`dag-pilot`),
  `run_mode`, `capacity` (1 to 3), `agent_deadline_seconds` (300/600/900).
  `contents: read`, no secrets, no token, no push/PR/merge/deploy.
- Round job `plan`: downloads this run's evidence, asks the scheduler for at
  most `capacity` ready tasks. Job `worker`: one runner per task, hard
  deadline, evidence upload named
  `ocfilo-f2-<run>-<attempt>-r<round>-<task>`. Empty round: worker skipped.
- `config-only` runs round 1 only and makes no model call. `execute` runs up to
  6 rounds; later rounds start even after a failure (unless cancelled) so
  independent branches finish, retries happen within the attempt budget, and
  dependents of a failed task stay blocked.
- Final `state` job writes `ocfilo-f2-dagstate-<run>-<attempt>` and, in
  `execute` mode, fails the run unless every task `SUCCEEDED`.
- `concurrency: ocfilo-f2-dag-run` allows one DAG run at a time, so quota is
  not shared by overlapping runs.
- Capacity is capped per registered plan in `DAG_PLANS`. `dag-pilot` and
  `capacity-5` are capped at 5, validated by two clean 5-worker runs (see
  `docs/ocfilo-f2-capacity.md`). `capacity-10` (ten independent tasks) is capped
  at 10 and exists only to take the 10-worker measurement. A cap is raised only
  after a clean measured run at that width; asking for more than a plan's cap
  is rejected.

## F2.4 - Verified integration assembler (local, human-reviewed)

`.github/scripts/ocfilo_f2_integrate.py` turns the artifacts of ONE complete,
successful DAG run into a single local branch. It has no network code, never
pushes, never opens a PR and never merges (`pushed=false`,
`review_required=true`, `merge_allowed=false` in its manifest). Pushing the
branch and opening the PR is a separate human-supervised step.

Everything is checked before any git change:

- **Run metadata** (the GitHub runs API JSON, fetched by the operator): id,
  attempt, `head_sha` equal to the evidence SHA, `head_branch=main`,
  `event=workflow_dispatch`, path of the DAG run workflow, `status=completed`,
  `conclusion=success`, repository name and numeric ID.
- **Artifact identity**: only `ocfilo-f2-<run>-<attempt>-r<round>-<task>`
  folders (plus the ignored `dagstate` one), only `result.json` and
  `changes.patch`, no symlinks. The folder name must agree with the evidence.
  A patch must exist exactly when the task `SUCCEEDED`.
- **Evidence**: schema-2 contract (`validate_evidence`) against the plan, exact
  SHA, same attempt. Scheduler state must be `COMPLETE`; exactly one
  `SUCCEEDED` evidence per task. Any failed, missing or duplicate task refuses
  the whole integration (no partial branches).
- **Patch shape**: UTF-8 text, at most 32 KiB, one `diff --git` for exactly the
  owned file, new file or plain `100644` change, no rename, delete, mode
  change, symlink or binary data, no stray lines. Each patch must also pass
  `git apply --check` on the exact base.
- **Repository**: `HEAD` is the evidence SHA, worktree clean, branch name
  `f2-integration/<name>` and not already present. After applying, the staged
  change set must equal the owned paths exactly; otherwise the branch is
  rolled back and deleted.
- Commit author is `ocfilo-integrator`, separate from any GitHub credential.

Usage: download the run's artifacts (`gh run download <run>`), save
`gh api repos/<repo>/actions/runs/<run>` to a file, check out the evidence SHA
and run `ocfilo_f2_integrate.py --plan <name> --artifacts <dir> --run-json <file>
--sha <sha> --manifest <out.json> [--branch f2-integration/<name>]`. Without
`--branch` it is a dry run. CI on the resulting PR runs normally.

## Manual acceptance

1. After code review and green baseline, F2.2 tests and Semgrep, merge
   the PR by expected head SHA. Do not bypass `main` protection.
2. GitHub Actions  `ocfilo F2 Bounded Manual OpenCode Pilot`  Run
   workflow  `main`  `worker_count=1`  `config-only`. Confirm the
   trusted Actions run produces one `CONFIG_ONLY` evidence artifact
   with the current checkout SHA and **no patch**.
3. Repeat with `worker_count=1`, `run_mode=execute`. Expect one
   `SUCCEEDED` patch **only if** free model access works, and confirm
   its exact file path. On provider error, record blocker; never invent
   success or switch to a paid model/token.
4. Only after successful 1-worker execution, choose `worker_count=3`
   and `execute`. Verify three independent jobs run concurrently when
   runner quota allows and produce three disjoint scoped patches, run
   provenance, and no GitHub changes. Recheck GitHub runner quota/rate limit.
5. Independently read artifact run ID, job conclusion, current head SHA,
   issue number, exact file owner. Fail closed if any mismatch or missing
   check; no automatic combining/publishing or deploy.

## Next F2 acceptance gates

- F2.3: measured capacity, fair task lease/queues, bounded retries,
  error taxonomy, cancel support and cost/rate-limit budget before 510.
- F2.4: cross-job artifact verification, deterministic conflict handling,
  integration tests and human-reviewed PR, never automatic merge.
- Multica remains optional. Previous native Autopilot safety denial is
  still outstanding in GitHub Issue #6 and must not be bypassed.
