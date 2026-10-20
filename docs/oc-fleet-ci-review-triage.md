# Read-only PR CI/review triage

The fleet's initial production PR triage is a standalone, read-only component
for exactly two repositories: oaslananka/adxl355 (real Python project) and
oaslananka-dev/oc-test (fleet acceptance fixtures).

From an operator checkout:

    python3 .github/scripts/oc_fleet_pr_triage.py --repo oaslananka/adxl355 --pr 123

The optional --expected-sha argument refuses an unexpected PR HEAD. The
observer reads PR identity, the current SHA's check-runs and commit statuses,
review decisions and comment metadata, then re-reads PR HEAD. Missing or
paginated evidence fails closed. Nothing is sent to an AI model, nothing
is written to GitHub, and no worker or merge action is triggered.

## Decision output

| Primary state | Evidence | Next action |
| --- | --- | --- |
| WAITING_CI | A current check or status has not finished | Wait |
| FAILED_CI | A current check or status failed | Inspect actual CI logs |
| REVIEW_TRIAGE | Review request, comment, or changes requested | Evaluate feedback |
| MERGE_CONFLICT | GitHub merge conflict | Resolve separately |
| BASE_BEHIND | PR behind base branch | Assess base update |
| OBSERVED_GREEN | Observed results passed, clean merge status | Still obey required checks and branch policy |
| INSUFFICIENT_EVIDENCE | No current checks or statuses | Investigate |
| UNKNOWN_MERGE_STATE | GitHub merge state unavailable | Investigate |
| CLOSED | PR no longer open | No repair |

Check families such as DEPENDENCY_SECURITY, QUALITY_COVERAGE, TEST_BUILD,
SECURITY_STATIC and OTHER_CI are *signals*, never root-cause diagnoses.
For example, failing Node.js jobs do not necessarily indicate a dependency
vulnerability. A failed check on an old head SHA is ignored. A repeat
check with a newer unique GitHub check ID supersedes the previous attempt;
unresolvable conflicts remain blockers.

Human, bot and unknown inline/discussion comments are counted separately
from publisher metadata, but the untrusted comment body is never inserted
into a command or agent prompt. Comment presence is not proof of an
unresolved actionable request. Review decisions are tracked separately.

**All output explicitly sets dispatch_allowed=false.** Real production
repositories do not get auto-repair or auto-merge privileges from this
extension. The separately deployed F1 repair mechanism is still limited
to one PR, one expected check and one fixture path. Its own SHA, trusted
GitHub App publisher, retry reservation and patch checks remain mandatory.

## First live observation, 2026-10-20

- adxl355 #123 and #118: dependency/security and test/build failures, behind main.
- adxl355 #117: Go coverage failure and merge conflict.
- oc-test #1 and #2: observed clean, no new work.

These results are point-in-time only; re-fetch before any decision.
