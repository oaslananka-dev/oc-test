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

## How to repeat

`ocfilo F2 Bounded Manual DAG Run` on `main`: `plan=capacity-5` or
`capacity-10`, `run_mode=execute`, matching `capacity`, `agent_deadline_seconds=300`.
Then download the artifacts and read `agent_seconds`, `failure_class` and the
job start/finish times.

## Real-runner cancel (run 38365148389)

A 5-worker `capacity-5` run was cancelled with `gh run cancel` while the agents
were running. Result: all 5 worker jobs `cancelled`, and all 5 still uploaded
valid evidence: `worker_outcome=CANCELLED`, `failure_class=CANCELLED`,
`retryable=false`, `scope_verified=false`, `published=false`, no patch. One
agent had already exited 0 after 13 s when the cancel arrived; it was still
recorded as `CANCELLED` and its output discarded, which is the conservative
choice. Later rounds did not start (`!cancelled()` guard). Cancel recovery is
therefore validated on a real runner for this workflow.
