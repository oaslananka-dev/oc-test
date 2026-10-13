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

For every run: scope verified, `published=false`, one owned file per worker, no
failures, no retries, no 429/5xx observed.

## Decisions

- **Cap 5 is validated** (`VALIDATED_CAPACITY = 5`) after two clean 5-worker
  runs. Evidence is thin (two runs, no provider errors seen), so the 5-worker
  claim means "worked twice", not "will always work".
- **Cap 10 is NOT validated.** The `capacity-10` plan exists only to take the
  measurement. If a 10-worker run shows 429/5xx, quota errors or timeouts,
  record them here and keep the cap at 5.
- Failure paths (timeout kill, provider errors, retry across rounds) have not
  occurred on a real runner yet; they are covered by offline tests only.

## How to repeat

`ocfilo F2 Bounded Manual DAG Run` on `main`: `plan=capacity-5` or
`capacity-10`, `run_mode=execute`, matching `capacity`, `agent_deadline_seconds=300`.
Then download the artifacts and read `agent_seconds`, `failure_class` and the
job start/finish times.
