# ocfilo F2 capacity measurements

Measured on GitHub-hosted `ubuntu-24.04` runners with the free
`opencode/big-pickle` model, repository `oaslananka-dev/oc-test`, 2026-10-13.
Numbers describe those runs only. They are not a guarantee: free-model latency
varied from 10 s to 261 s in earlier runs, and provider quota is outside our
control. A cap is raised only after clean runs at that width.

| Run | Plan | Workers | Started within | Agent seconds (min / median / max) | Result |
|---|---|---:|---|---|---|
| [38353007198](https://github.com/oaslananka-dev/oc-test/actions/runs/38353007198) (F2.2) | pilot | 3 | 1 s | 39 / 221 / 261 | 3 SUCCEEDED |
| [38358793590](https://github.com/oaslananka-dev/oc-test/actions/runs/38358793590) | dag-pilot (3 rounds) | 1, 2, 1 | 1 s in round 2 | 10 / 16 / 63 | COMPLETE |
| [38359600536](https://github.com/oaslananka-dev/oc-test/actions/runs/38359600536) | capacity-5 | 5 | 0 s (all 06:56:34Z) | 15 / 19 / 26 | 5 SUCCEEDED |
| [38359780386](https://github.com/oaslananka-dev/oc-test/actions/runs/38359780386) | capacity-5 | 5 | 0 s (all 06:58:28Z) | 15 / 18 / 49 | 5 SUCCEEDED |
| [38364810638](https://github.com/oaslananka-dev/oc-test/actions/runs/38364810638) | capacity-5 | 5 | 0 s (all 07:46:34Z) | 13 / 20 / 67 | 5 SUCCEEDED |
| [38360448016](https://github.com/oaslananka-dev/oc-test/actions/runs/38360448016) | capacity-10 | 10 | 4 s (07:05:06Z to 07:05:10Z) | 12 / 18.5 / 247 | 10 SUCCEEDED |
| [38361074538](https://github.com/oaslananka-dev/oc-test/actions/runs/38361074538) | capacity-10, 600 s deadline | 10 (+1 retry) | 6 s (07:11:14Z to 07:11:20Z) | 14 / 18 / 28 for the 9 that finished | 9 SUCCEEDED, 1 FAILED, DAG HALTED |

Scope was verified and `published=false` for every worker in every run.
Runs up to and including the first 10-worker run had no failures or retries.

### Run 38361074538 in detail (the first real failure)

- Nine tasks finished in 14 to 28 s. `f2-cap10-e` ran until the 600 s deadline:
  exit code 124, 601 s, `failure_class=TIMEOUT`, `retryable=true`. This is the
  first time the timeout kill path ran on a real runner; it behaved as designed
  and the evidence was still written and uploaded.
- The scheduler proposed one retry in round 2 (alone, capacity free). That
  attempt failed after 32 s with exit code 1, `failure_class=AGENT_ERROR`
  (`retryable=false`). The cause is unknown: the log patterns did not match
  and raw logs are not uploaded.
- Retry budget (2) was spent, so the task became `FAILED`, the DAG outcome was
  `HALTED` and the final `state` job failed the run. Nothing was published.
- Follow-up: the round workflow now prints a bounded, command-safe tail of the
  agent log to the job log on failure (never to an artifact), so the next
  `AGENT_ERROR` can be diagnosed.

### 21 October 2026: ten-worker recovery measurement

- Real [Actions run 39610361972](https://github.com/oaslananka-dev/oc-test/actions/runs/39610361972) was a single manually authorized capacity-10 execution on main SHA
  03d487448e6fb55d5d40d0d9f826765db135d323, with the free
  opencode/big-pickle model, a 600-second hard deadline, and at most two
  attempts per task.
- Ten separate GitHub worker jobs started together at 13:42:48 UTC; nine
  completed successfully in round 1. Task f2-cap10-e again exceeded 600
  seconds and failed; only that task retried, succeeding in round 2.
- GitHub verified ten eventually successful worker jobs, one earlier failed
  attempt, and a successful final state job. The overall workflow was
  **FAILURE** due to the first attempt, so it does **not** meet the strict
  zero-timeout 10/10 acceptance and is not eligible for trusted publication.
- The Ops GitHub App initially confirmed 12 artifact names and SHA-256
  digests; personal GitHub API rate limiting delayed byte-level validation.
  Once the quota reset on 21 October, all 12 artifacts were downloaded and
  independently replay-checked on AMD2700X with the approved capacity-10 plan
  and exact source SHA. The 11 schema-2 worker records, round/run/attempt
  provenance, 10 exact-owner patches and saved final DAG state all passed.
  Independently recalculated final state matched the downloaded JSON exactly:
  COMPLETE, 10 SUCCEEDED, one earlier TIMEOUT attempt (exit 124 / 600 s),
  and an f2-cap10-e retry in round 2 (64 agent seconds). The GitHub workflow
  itself remains FAILURE and strict first-try 10/10 is NOT ACCEPTED.
- Repeated timeout of e does not establish a root cause: provider/network
  stall, repeated model loop and excessive tool use remain hypotheses.
  External issue reports about free Big Pickle hangs are not proof for this run.
- Follow-up changes add targeted local-only DEBUG logging for just the affected
  sandbox task and fixed-field non-content diagnostics when the worker fails.
  They do not change the fixed model, capacity, deadline, retry allowance,
  credentials, or fail-closed artifact checks. Cap 5 remains validated.

### Recovery workflow conclusion correction (offline-only)

The historical run #39610361972 remains a GitHub FAILURE and is NOT
retrospectively reclassified. Its first retryable worker failure and the
expected nonzero exit from finalize both made the round job fail, even
though a later retry completed the DAG. The reusable round workflow now
uses step-level continue-on-error ONLY on those two steps. GitHub keeps
the actual model error in steps.opencode.outcome=failure; finalize retains
FAILED/TIMEOUT evidence for the next scheduler round. The artifact upload
still fails if nothing was produced. Setup, scope checks, round planning and
the required final DAG state retain strict failures.

Only the final state with --require-complete may make an executing DAG
workflow green; exhausted retries, bad or missing evidence, failed artifact
upload, and cancellation must not be silently accepted. This change has
offline workflow/scheduler regression coverage ONLY: its GitHub
job-conclusion behavior has NOT yet been revalidated on a new live run.
It does not alter the 29 first-try-clean independent-run criterion for
capacity-10 promotion, nor authorize running a new model experiment.

## Decisions

- **Cap 5 is validated** (`VALIDATED_CAPACITY = 5`) after two clean 5-worker
  runs. Evidence is thin (two runs, no provider errors seen), so the 5-worker
  claim means "worked twice", not "will always work".
- **Cap 10 is NOT validated; the cap stays at 5.** GitHub started 10 runners
  within 4 s and 6 s, so runner capacity is not the limit. The model is: of
  20 agents in the two 10-worker runs, one ran past 247 s (close to a 300 s
  deadline) and one hung to the 600 s deadline and then failed on retry. Two
  runs, one of them failed, so there is no basis to call 10 reliable.
- Retrying a hung task a second time can fail for unrelated reasons; the retry
  budget bounded the damage as intended.
- Timeout kill path and retry across rounds have now happened once on a real
  runner. Provider error classes (429/5xx/quota) still have not been observed;
  they are covered by offline tests only.
- To validate 10: several clean runs, preferably at different times of day.
  Until then use 5, or treat a 10-wide run as best effort and expect a possible
  `HALTED` state.

## 21 October 2026: four-run historical reliability analysis

To make capacity decisions reproducible without another model execution,
read-only GitHub Actions job metadata is preserved in
docs/ocfilo-f2-capacity-10-runs.json. Recalculate it offline:

    python3 .github/scripts/ocfilo_f2_capacity_stats.py --snapshot docs/ocfilo-f2-capacity-10-runs.json

Metadata was retrieved from the original GitHub runs on 21 October.
The 13 October artifacts have expired and could not be replayed; their
results originate from GitHub job conclusions and contemporaneous
documentation. Both 21 October artifact bundles passed independent
strict byte-level replay. The snapshot is static; the analyzer cannot
authenticate its source, dispatch workers or grant capacity authorization.

| Actions run | Date UTC | First round | Retry jobs | Workflow | Max job wall time |
|---|---|---|---|---|---|
| 38360448016 | 13 Oct | 10/10 | 0 | SUCCESS | 263 s |
| 38361074538 | 13 Oct | 9/10 | 1 (failed) | FAILURE | 616 s |
| 39610361972 | 21 Oct | 9/10 | 1 (succeeded) | FAILURE | 619 s |
| 39623759336 | 21 Oct | 10/10 | 0 | SUCCESS | 412 s |

Observed: **2/4 clean workflows**, **38/40 first-round successful workers**,
two first-round timeouts, two retry jobs and three failed job attempts
(including the failed retry in the 13 October run). Across the 40
first-round GitHub *worker job wall times*: min 28 s, median 61.5 s,
nearest-rank p90 263 s, nearest-rank p95 425 s, max 619 s. Job wall time
includes runner setup/finalization and is NOT exact model response time.
The median obscures pronounced long-tail latency. These runs span only
two dates and multiple commits/configurations. Ten worker attempts within
one run are correlated, NOT forty independent capacity-reliability trials.

### Proposed operational promotion gate — NOT run authorization

1. Preserve validated F2 capacity of five and production dispatcher capacity
   of four. Existing observations include real failures. No promotion now.
2. Define a prospective cohort with comparable pinned CLI/model/plan, runner
   image, security conditions and deadlines. Reset the cohort when a
   material configuration change occurs; never cherry-pick only successes.
3. Require **29 consecutive independent clean ten-worker workflows** spread
   across multiple days and UTC time blocks, never a burst of repeats:
   all ten succeed on first attempt, zero timeout or retry, exact-owned
   artifacts independently replay to COMPLETE, original GitHub conclusion
   SUCCESS, and no paid provider or publication. Failure breaks the streak.
   For the conservative metadata-only precheck, require **at least 5
   distinct UTC days, 3 distinct six-hour UTC clock bins, one hour between
   run dispatches, the identical exact checkout SHA, and independently
   replayed artifacts for every run**. These are *proposed* observable
   spacing/configuration criteria, not a proof of statistical independence.
   For the conservative metadata-only precheck, require **at least 5
   distinct UTC days, 3 distinct six-hour UTC clock bins, one hour between
   run dispatches, the identical exact checkout SHA, and independently
   replayed artifacts for every run**. These are *proposed* observable
   spacing/configuration criteria, not a proof of statistical independence.
4. As an engineering guardrail, require all first-round GitHub worker job
   wall times no more than **480 seconds** with the approved 600-second
   *agent* deadline. The proposed 120-second difference is not a provider
   SLA; job wall time includes more than agent time. Do not automatically
   raise deadlines, quota or worker counts.
5. At 29/29 independent successful run-level trials, the *one-sided exact
   95% binomial lower confidence bound* exceeds 90%. For a 95% lower bound,
   at least **59/59** independent clean runs would be needed. The current
   four-run observation of 2/4 clean has an illustrative one-sided 95%
   lower bound of 9.76%, but identical-distribution assumptions DO NOT
   hold for this mixed historical sample. Even a clean prospective
   cohort provides no permanent guarantee if quota/provider load changes.
6. Each future 10-ajan live execution consumes GitHub runner minutes
   and model quota. **Separate user approval is required for each**
   additional live experiment. No automatic cadence, hidden test runs,
   paid fallback, or newly provisioned resources.

The analyzer now also reports the field
prospective_candidate_cohort_precheck_not_authorization with explicit
rejection reasons and the verified-artifact subset of clean historical runs.
The current four-run dataset **FAILS** this precheck, including because
only **1 of the 2 apparently clean runs** has independently replayed artifact
evidence. The other clean October 13 run has authentic GitHub success
metadata, but its artifacts expired and cannot be retrospectively verified.

Even an entirely synthetic 29/29 metadata set that passes these offline
checks **cannot authorize operational promotion**: the script cannot prove
a complete unselected run list, authentic GitHub provenance or unchanged
model/CLI/runner behavior. These facts, true time-block independence and
the user's resource/authorization approval need human/source verification.
The output keeps promote_capacity_to_ten_authorized=false and
new_live_runs_authorized=false unconditionally.

The analyzer now also reports the field
prospective_candidate_cohort_precheck_not_authorization with explicit
rejection reasons and the verified-artifact subset of clean historical runs.
The current four-run dataset **FAILS** this precheck, including because
only **1 of the 2 apparently clean runs** has independently replayed artifact
evidence. The other clean October 13 run has authentic GitHub success
metadata, but its artifacts expired and cannot be retrospectively verified.

Even an entirely synthetic 29/29 metadata set that passes these offline
checks **cannot authorize operational promotion**: the script cannot prove
a complete unselected run list, authentic GitHub provenance or unchanged
model/CLI/runner behavior. These facts, true time-block independence and
the user's resource/authorization approval need human/source verification.
The output keeps promote_capacity_to_ten_authorized=false and
new_live_runs_authorized=false unconditionally.

This policy is a proposed measurable gate, not a claim that 10-agent
operational concurrency is already reliable or permission to promote it.
No further workflow dispatch, scheduled monitoring, background model use,
paid fallback or new infrastructure is authorized by this change.
No further workflow dispatch, scheduled monitoring, background model use,
paid fallback or new infrastructure is authorized by this change.

## 21 October 2026: resource feasibility for prospective 29-run cohort

A separate bounded read-only GitHub Actions resource snapshot records the four
historical real-run job durations, standard Ubuntu runner label and currently
available artifact ZIP sizes:
docs/ocfilo-f2-capacity-10-resources.json. Regenerate a scenario report with
no network or model calls:

    python3 .github/scripts/ocfilo_f2_capacity_resources.py --snapshot docs/ocfilo-f2-capacity-10-resources.json --planned-runs 29

| Manual run | Executed job time sum | Per-job rounded minutes (reference only) | Artifact ZIP bytes available |
|---|---:|---:|---:|
| 38360448016 | 10.28 runner-min | 21 | Unavailable/expired |
| 38361074538 | 16.92 runner-min | 28 | Unavailable/expired |
| 39610361972 | 29.12 runner-min | 43 | 14,833 |
| 39623759336 | 19.00 runner-min | 31 | 13,669 |

Executed job time is the **sum across concurrently executing jobs**, not
workflow wall-clock duration. Jobs skipped by GitHub are not charged any
execution time in this estimate (some API entries report reversed one-second
timestamps for skipped jobs). Per-job rounded minutes are reference-only,
not invoice data for this public repository.

Observed mean: **18.83 runner-minutes/run**. A 29-run cohort would be about
**546 runner-minutes** at the historical observed mean, or **844
runner-minutes** if every run were as heavy as the slowest recorded one.
These are extrapolated *resource use*, not guaranteed runtime or bill.
There are **290 first-round worker sessions** for 29 runs; allowing one
bounded retry on every worker yields a theoretical maximum of **580 worker
sessions**. A session may issue multiple provider inference requests; those
counts cannot be extrapolated from GitHub job metadata. A 29-run cohort must
span at least five distinct UTC dates and multiple time blocks; it is not
a single 29-run batch.

**Current cost boundaries (must be rechecked before every approved run):**

- The repository was **public** and used only standard GitHub-hosted
  ubuntu-24.04 runners in the measured workflows. GitHub currently lists
  these runner minutes as free for public repositories:
  https://docs.github.com/en/billing/concepts/product-billing/github-actions
  Larger runners are chargeable, including for public repositories.
- OpenCode currently lists Big Pickle as free for input/output tokens:
  https://opencode.ai/v2/docs/console/models/
  This is **not** an unlimited-use, rate-limit, duration or future-pricing
  guarantee. Keep the exact opencode/big-pickle selection; fail closed if
  unavailable or paid, with NO paid fallback or auto-recharge.
- The two unexpired GitHub artifact ZIP batches total just 13.7–14.8 KB per
  run. At the largest observed ZIP size, 29 similarly small runs would
  use about **0.41 MiB** of stored artifacts, assuming the same plan/output
  size. This is a *scenario*, not a verified storage ceiling. The workflow
  retains artifacts for **7 days**; Actions artifact storage is pooled with
  GitHub Packages and may create charges if an account's storage budget is
  exceeded. Historical expired artifacts must not be treated as 0-byte runs.
- Free-provider traffic can be throttled and may use submitted data for model
  improvement. Only the bounded synthetic, nonsecret capacity-manifest
  content should be used; do not switch to private source code or new
  providers without an explicit separate decision.

**Practical gated alternative to an up-front 29-run commitment:**

1. **Preflight each time**: manually reconfirm public repo, standard runner,
   current OpenCode model price/availability, privacy, artifact allowance,
   run SHA, CI permissions and no overlapping F2 run. If any check fails,
   do not dispatch.
2. **Stage A (3 prospective runs)**: if separately approved one by one,
   collect three comparable first-try-clean runs across distinct UTC days
   and bins, with independently replayed artifacts and job wall time at most
   480 s. Stop and investigate on the first failure/timeout or cost concern.
3. **Stage B (up to 10)**: only after reviewing Stage A evidence and explicit
   approval, extend the same pinned cohort to 10 total runs. A changed
   model/runner/plan resets the statistical cohort.
4. **Stage C (up to 29)**: consider the full 29 independent clean run-level
   acceptance only if earlier stages are consistently clean, practical and
   separately approved. Never auto-dispatch the remaining runs. The
   confidence calculation remains conditional on comparable independent
   trials; passing the 29-run local check STILL does not grant production
   capacity promotion.

Given observed **2/4** clean historical workflows and the lack of a current
operational need for ten production workers, the lower-cost/default choice
is to **retain validated F2=5 and production Fleet=4**, using 10 workers only
as approved manual sandbox experiments. No recurring schedule, paid model,
background job, new resources or production promotion follows from this
analysis.

## How to repeat

`ocfilo F2 Bounded Manual DAG Run` on `main`: `plan=capacity-5` or
`capacity-10`, `run_mode=execute`, matching `capacity`, `agent_deadline_seconds=300`.
Then download the artifacts and read `agent_seconds`, `failure_class` and the
job start/finish times.

### Independent, read-only replay of downloaded capacity-10 artifacts

After downloading all artifacts from a trusted workflow run into a separate
directory, use the registered plan, known GitHub run/attempt and exact source
SHA. This command never contacts the model or GitHub:

~~~bash
python3 .github/scripts/ocfilo_f2_capacity_audit.py --artifacts /path/to/downloaded-run-artifacts --run-id 39610361972 --run-attempt 1 --sha 03d487448e6fb55d5d40d0d9f826765db135d323
~~~

The auditor verifies strict evidence JSON, per-file task ownership, ordinary
non-executable single-file patch shape, run/attempt/SHA consistency and a
full byte-for-byte-equivalent scheduler replay of the saved DAG-state JSON.
It intentionally reports both outcome and first_try_all_ten. It CANNOT
authenticate who supplied the directory, check GitHub's overall workflow
conclusion, grant publication/merge authority, or promote the operational
five-worker limit. Cross-check the original run and its CI conclusion
separately before making any acceptance decision.

## Real-runner cancel (run 38365148389)

A 5-worker `capacity-5` run was cancelled with `gh run cancel` while the agents
were running. Result: all 5 worker jobs `cancelled`, and all 5 still uploaded
valid evidence: `worker_outcome=CANCELLED`, `failure_class=CANCELLED`,
`retryable=false`, `scope_verified=false`, `published=false`, no patch. One
agent had already exited 0 after 13 s when the cancel arrived; it was still
recorded as `CANCELLED` and its output discarded, which is the conservative
choice. Later rounds did not start (`!cancelled()` guard). Cancel recovery is
therefore validated on a real runner for this workflow.
