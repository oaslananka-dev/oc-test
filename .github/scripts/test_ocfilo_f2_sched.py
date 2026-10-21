#!/usr/bin/env python3
"""F2.3b offline DAG scheduler tests: readiness, blocking, retry, capacity."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import ocfilo_f2_pilot as pilot
import ocfilo_f2_sched as sched

ROOT = Path(__file__).resolve().parents[2]
SHA = "c" * 40
A, B, C, D = (f"f2-s-{x}" for x in "abcd")
PATHS = {t: f"playground/s/{t[-1]}.txt" for t in (A, B, C, D)}
DEPS = {A: [], B: [A], C: [A], D: [B, C]}


def make_plan():
    return {
        "schema_version": 1, "plan_id": "f2-s", "repository": pilot.REPO,
        "run_mode": "plan-only", "max_parallel": 10,
        "tasks": [{"task_id": t, "prompt": "Write the file " + PATHS[t],
                   "model": pilot.MODEL, "agent": "build", "profile": "base",
                   "identity": "actions-readonly", "allowed_paths": [PATHS[t]],
                   "depends_on": DEPS[t], "issue_number": 10}
                  for t in (A, B, C, D)],
    }


def ev(tid, outcome="SUCCEEDED", cls=None, run=100, attempt=1, sha=SHA):
    ran = outcome not in ("CONFIG_ONLY",)
    return {
        "schema_version": 2, "repository": pilot.REPO,
        "repository_id": int(pilot.REPO_ID), "issue_number": 10,
        "model": pilot.MODEL, "identity": "actions-readonly",
        "allowed_paths": [PATHS[tid]], "task_id": tid, "checkout_sha": sha,
        "idempotency_key": pilot.idempotency_key(sha, tid),
        "workflow_run_id": run, "run_attempt": attempt,
        "worker_outcome": outcome, "failure_class": cls,
        "retryable": cls in pilot.RETRYABLE,
        "agent_exit_code": (0 if outcome == "SUCCEEDED" else 1) if ran else None,
        "agent_seconds": 10 if ran else None,
        "deadline_seconds": 300 if ran else None,
        "scope_verified": outcome == "SUCCEEDED", "published": False,
    }


def fail(tid, cls="TIMEOUT", run=100):
    return ev(tid, "FAILED", cls, run=run)


def step(evidence=(), **kw):
    kw.setdefault("capacity", 3)
    return sched.schedule(make_plan(), list(evidence), SHA, **kw)


def states(result):
    return {t: v["state"] for t, v in result["tasks"].items()}


class ReadinessTests(unittest.TestCase):
    def test_start_dispatches_only_roots(self):
        r = step()
        self.assertEqual(r["dispatch"], [A])
        self.assertEqual(states(r), {A: "READY", B: "PENDING", C: "PENDING", D: "PENDING"})
        self.assertEqual(r["outcome"], "IN_PROGRESS")

    def test_dependents_ready_after_success(self):
        r = step([ev(A)])
        self.assertEqual(r["dispatch"], [B, C])
        self.assertEqual(states(r)[D], "PENDING")

    def test_capacity_limits_dispatch(self):
        self.assertEqual(step([ev(A)], capacity=1)["dispatch"], [B])

    def test_running_consumes_capacity_and_is_not_redispatched(self):
        r = step([ev(A)], capacity=2, running=[B])
        self.assertEqual(states(r)[B], "RUNNING")
        self.assertEqual(r["dispatch"], [C])
        r = step([ev(A)], capacity=3, running=[B, C])
        self.assertEqual(r["dispatch"], [])
        self.assertEqual(r["outcome"], "IN_PROGRESS")

    def test_full_capacity_of_running_tasks_blocks_dispatch(self):
        r = step([ev(A)], capacity=1, running=[B])
        self.assertEqual(states(r)[C], "READY")
        self.assertEqual(r["dispatch"], [])
        r = step([ev(A)], capacity=2, running=[B, C])
        self.assertEqual(r["dispatch"], [])

    def test_succeeded_never_redispatched_even_if_running_listed(self):
        r = step([ev(A)], running=[A])
        self.assertEqual(states(r)[A], "SUCCEEDED")
        self.assertNotIn(A, r["dispatch"])

    def test_duplicate_identical_success_is_idempotent(self):
        r = step([ev(A), ev(A)])
        self.assertEqual(states(r)[A], "SUCCEEDED")
        self.assertEqual(r["dispatch"], [B, C])

    def test_complete(self):
        r = step([ev(t) for t in (A, B, C, D)])
        self.assertEqual(r["outcome"], "COMPLETE")
        self.assertEqual(r["dispatch"], [])

    def test_config_only_is_not_an_attempt(self):
        r = step([ev(A, "CONFIG_ONLY")])
        self.assertEqual(states(r)[A], "READY")
        self.assertEqual(r["tasks"][A]["attempts"], 0)

    def test_evidence_order_does_not_change_result(self):
        e = [ev(A), fail(B, run=101), ev(B, run=102)]
        self.assertEqual(json.dumps(step(e), sort_keys=True),
                         json.dumps(step(e[::-1]), sort_keys=True))


class FailureHandlingTests(unittest.TestCase):
    def test_retryable_failure_is_retried_once(self):
        r = step([ev(A), fail(B)])
        self.assertEqual(states(r)[B], "READY")
        self.assertEqual(r["tasks"][B]["attempts"], 1)
        self.assertEqual(r["tasks"][B]["last_failure_class"], "TIMEOUT")
        self.assertIn(B, r["dispatch"])

    def test_retry_budget_is_bounded(self):
        r = step([ev(A), fail(B, run=100), fail(B, run=101)])
        self.assertEqual(states(r)[B], "FAILED")
        self.assertEqual(r["tasks"][B]["attempts"], 2)
        self.assertNotIn(B, r["dispatch"])

    def test_quota_is_never_retried(self):
        r = step([ev(A), fail(B, "QUOTA_EXHAUSTED")])
        self.assertEqual(states(r)[B], "FAILED")

    def test_scope_violation_is_never_retried(self):
        self.assertEqual(states(step([ev(A), fail(B, "SCOPE_VIOLATION")]))[B], "FAILED")

    def test_cancelled_is_terminal(self):
        r = step([ev(A), ev(B, "CANCELLED", "CANCELLED")])
        self.assertEqual(states(r)[B], "CANCELLED")
        self.assertEqual(states(r)[D], "BLOCKED")

    def test_failure_then_success_is_success(self):
        r = step([ev(A), fail(B, run=100), ev(B, run=101)])
        self.assertEqual(states(r)[B], "SUCCEEDED")

    def test_failed_dependency_blocks_downstream_only(self):
        r = step([ev(A), fail(B, "QUOTA_EXHAUSTED")])
        s = states(r)
        self.assertEqual((s[B], s[D]), ("FAILED", "BLOCKED"))
        self.assertEqual(s[C], "READY")           # independent work continues
        self.assertEqual(r["dispatch"], [C])
        self.assertEqual(r["tasks"][D]["blocked_by"], [B])
        self.assertEqual(r["outcome"], "IN_PROGRESS")

    def test_halted_when_nothing_can_progress(self):
        r = step([ev(A), fail(B, "QUOTA_EXHAUSTED"), ev(C)])
        self.assertEqual(states(r)[D], "BLOCKED")
        self.assertEqual(r["dispatch"], [])
        self.assertEqual(r["outcome"], "HALTED")

    def test_blocked_propagates_transitively(self):
        r = step([fail(A, "QUOTA_EXHAUSTED")])
        self.assertEqual(states(r), {A: "FAILED", B: "BLOCKED", C: "BLOCKED", D: "BLOCKED"})
        self.assertEqual(r["tasks"][D]["blocked_by"], [B, C])
        self.assertEqual(r["outcome"], "HALTED")

    def test_retry_attempts_counted_per_distinct_run(self):
        r = step([ev(A), fail(B, run=100), fail(B, run=100)])
        self.assertEqual(r["tasks"][B]["attempts"], 1)
        self.assertEqual(states(r)[B], "READY")


class FailClosedTests(unittest.TestCase):
    def reject(self, evidence, **kw):
        with self.assertRaises(ValueError):
            step(evidence, **kw)

    def test_wrong_sha(self):
        self.reject([ev(A, sha="d" * 40)])

    def test_unknown_task(self):
        bad = ev(A)
        bad["task_id"] = "f2-s-zzz"
        self.reject([bad])

    def test_tampered_fields(self):
        for key, value in (("published", True), ("allowed_paths", ["playground/s/x.txt"]),
                           ("idempotency_key", "0" * 64), ("model", "paid/model"),
                           ("issue_number", 6), ("repository_id", 1)):
            bad = ev(A)
            bad[key] = value
            self.reject([bad])

    def test_non_dict_evidence(self):
        self.reject(["not-a-dict"])
        self.reject([None])

    def test_success_before_dependency_is_rejected(self):
        self.reject([ev(D)])
        self.reject([ev(A), ev(D)])

    def test_conflicting_duplicate_for_same_run(self):
        self.reject([fail(B, "TIMEOUT"), fail(B, "NETWORK")])

    def test_capacity_bounds(self):
        for bad in (0, 6, -1, True, "3", None):
            self.reject([], capacity=bad)

    def test_running_must_be_known_and_unblocked(self):
        self.reject([], running=["f2-s-zzz"])
        self.reject([], running=[D])          # dependencies not done

    def test_max_attempts_bounds(self):
        for bad in (0, 4, True):
            self.reject([], max_attempts=bad)

    def test_evidence_count_bounded(self):
        self.reject([ev(A, run=i) for i in range(1, sched.MAX_EVIDENCE + 2)])

    def test_invalid_plan_rejected(self):
        bad = make_plan()
        bad["tasks"][0]["model"] = "paid/model"
        with self.assertRaises(ValueError):
            sched.schedule(bad, [], SHA)


class OutputContractTests(unittest.TestCase):
    def test_output_is_proposal_without_prompts(self):
        r = step([ev(A)])
        text = json.dumps(r)
        self.assertIs(r["proposal_only"], True)
        self.assertNotIn("Write the file", text)
        self.assertEqual(r["checkout_sha"], SHA)
        self.assertEqual(r["plan_id"], "f2-s")

    def test_ten_task_example_first_step(self):
        raw = json.loads((ROOT / "examples/ocfilo-f2-ten-task-plan.json").read_text(encoding="utf-8-sig"))
        r = sched.schedule(raw, [], SHA)
        self.assertEqual(r["dispatch"], ["f2-pilot-audit"])
        self.assertEqual(len(r["tasks"]), 10)

    def test_cli_reads_evidence_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            plan.write_text(json.dumps(make_plan()), encoding="utf-8")
            art = root / "ev" / "one"
            art.mkdir(parents=True)
            (art / "result.json").write_text(json.dumps(ev(A)), encoding="utf-8")
            out = root / "out.json"
            sched.main(["--plan", str(plan), "--evidence-dir", str(root / "ev"),
                        "--sha", SHA, "--output", str(out)])
            self.assertEqual(json.loads(out.read_text())["dispatch"], [B, C])
            with self.assertRaises(SystemExit):
                sched.main(["--plan", str(plan), "--evidence-dir", str(root / "ev"),
                            "--sha", SHA, "--output", str(out)])  # never overwrite

    def test_scheduler_evidence_ingestion_rejects_ambiguous_json(self):
        valid = json.dumps(ev(A), separators=(",", ":"))
        malicious = (
            valid[:-1] + ',"task_id":"f2-s-zzz"}',
            valid[:-1] + ',"\\u0074ask_id":"f2-s-zzz"}',
            valid[:-1] + ',"unknown":true}',
            valid[:-1] + ',"extra":{"x":1,"x":2}}',
            valid[:-1] + ',"agent_seconds":NaN}',
            valid[:-1] + ',"agent_seconds":Infinity}',
            valid[:-1] + ',"agent_seconds":1e999}',
            '[]', 'null', '{invalid}',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            self.assertEqual(sched.load_evidence(tmp), [])
            path.write_text(valid, encoding="utf-8")
            self.assertEqual(sched.load_evidence(tmp), [ev(A)])
            for payload in malicious:
                with self.subTest(payload=payload[:96]):
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        sched.load_evidence(tmp)

    def test_cli_rejects_oversized_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plan.json").write_text(json.dumps(make_plan()), encoding="utf-8")
            (root / "ev").mkdir()
            (root / "ev" / "result.json").write_text("x" * 20000, encoding="utf-8")
            with self.assertRaises(SystemExit):
                sched.main(["--plan", str(root / "plan.json"), "--evidence-dir",
                            str(root / "ev"), "--sha", SHA,
                            "--output", str(root / "o.json")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
