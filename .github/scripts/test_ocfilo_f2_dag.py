#!/usr/bin/env python3
"""F2.3c offline tests: manifest-driven DAG worker contract and round matrix."""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import ocfilo_f2_dag as dag
import ocfilo_f2_pilot as pilot
import ocfilo_f2_sched as sched

ROOT = Path(__file__).resolve().parents[2]
RAW = json.loads((ROOT / "examples/ocfilo-f2-dag-pilot.json").read_text(encoding="utf-8-sig"))
SHA = "e" * 40
AUDIT, BACKEND, FRONTEND, REPORT = (f"f2-dag-{x}" for x in ("audit", "backend", "frontend", "report"))


def context(sha=SHA, run="777"):
    return {"GITHUB_REPOSITORY": pilot.REPO, "GITHUB_REPOSITORY_ID": pilot.REPO_ID,
            "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": sha, "GITHUB_RUN_ID": run, "GITHUB_RUN_ATTEMPT": "1"}


def evidence(tid, outcome="SUCCEEDED", cls=None, run=777, rnd=1, sha=SHA):
    path = {t: x["allowed_paths"] for t, x in dag.read_dag(RAW).items()}[tid]
    return {
        "schema_version": 2, "repository": pilot.REPO, "repository_id": int(pilot.REPO_ID),
        "issue_number": 10, "model": pilot.MODEL, "identity": "actions-readonly",
        "allowed_paths": path, "task_id": tid, "round": rnd, "checkout_sha": sha,
        "idempotency_key": pilot.idempotency_key(sha, tid),
        "workflow_run_id": run, "run_attempt": 1, "worker_outcome": outcome,
        "failure_class": cls, "retryable": cls in pilot.RETRYABLE,
        "agent_exit_code": 0 if outcome == "SUCCEEDED" else 1, "agent_seconds": 9,
        "deadline_seconds": 300, "scope_verified": outcome == "SUCCEEDED", "published": False,
    }


class ManifestTests(unittest.TestCase):
    def reject(self, mutate):
        raw = copy.deepcopy(RAW)
        mutate(raw)
        with self.assertRaises((ValueError, TypeError, KeyError)):
            dag.read_dag(raw)

    def test_reviewed_manifest_valid(self):
        tasks = dag.read_dag(RAW)
        self.assertEqual(set(tasks), {AUDIT, BACKEND, FRONTEND, REPORT})
        self.assertEqual(tasks[REPORT]["depends_on"], [BACKEND, FRONTEND])

    def test_registry_matches_manifest_file(self):
        for name, (path, plan_id, cap) in dag.DAG_PLANS.items():
            raw = json.loads((ROOT / path).read_text(encoding="utf-8-sig"))
            self.assertEqual(raw["plan_id"], plan_id)
            dag.read_dag(raw, plan_id)

    def test_wrong_plan_id(self):
        self.reject(lambda r: r.update(plan_id="f2-other"))

    def test_path_outside_dag_prefix(self):
        self.reject(lambda r: r["tasks"][0].update(allowed_paths=["playground/f2/audit.txt"]))

    def test_two_paths_rejected(self):
        self.reject(lambda r: r["tasks"][0].update(
            allowed_paths=["playground/f2/dag/audit.txt", "playground/f2/dag/x.txt"]))

    def test_issue_and_profile_and_model_pinned(self):
        self.reject(lambda r: r["tasks"][0].update(issue_number=6))
        self.reject(lambda r: r["tasks"][0].pop("issue_number"))
        self.reject(lambda r: r["tasks"][0].update(profile="runner-check"))
        self.reject(lambda r: r["tasks"][0].update(model="opencode/ling-3.1-flash-free"))

    def test_unapproved_extra_field(self):
        self.reject(lambda r: r["tasks"][0].update(mcp={"x": {}}))

    def test_cycle_and_overlap_still_rejected_by_planner(self):
        self.reject(lambda r: r["tasks"][0].update(depends_on=[REPORT]))
        self.reject(lambda r: r["tasks"][1].update(allowed_paths=r["tasks"][0]["allowed_paths"]))


class RoundMatrixTests(unittest.TestCase):
    def matrix(self, evid=(), rnd=1, capacity=3, deadline=300, env=None, sha=SHA):
        return dag.round_matrix(RAW, list(evid), env or context(sha), sha, rnd, capacity, deadline)

    def test_round_one_starts_root_only(self):
        m, r = self.matrix()
        self.assertEqual([x["task_id"] for x in m["include"]], [AUDIT])
        self.assertEqual(m["include"][0]["round"], 1)
        self.assertEqual(m["include"][0]["deadline_seconds"], 300)
        self.assertEqual(m["include"][0]["allowed_path"], "playground/f2/dag/audit.txt")

    def test_second_round_runs_independent_branches_in_parallel(self):
        m, _ = self.matrix([evidence(AUDIT)], rnd=2)
        self.assertEqual([x["task_id"] for x in m["include"]], [BACKEND, FRONTEND])
        self.assertEqual([x["slot"] for x in m["include"]], [1, 2])

    def test_capacity_caps_matrix(self):
        m, _ = self.matrix([evidence(AUDIT)], rnd=2, capacity=1)
        self.assertEqual([x["task_id"] for x in m["include"]], [BACKEND])

    def test_failure_blocks_join_but_not_sibling(self):
        e = [evidence(AUDIT), evidence(BACKEND, "FAILED", "QUOTA_EXHAUSTED")]
        m, r = self.matrix(e, rnd=3)
        self.assertEqual([x["task_id"] for x in m["include"]], [FRONTEND])
        self.assertEqual(r["tasks"][REPORT]["state"], "BLOCKED")

    def test_retry_in_later_round_uses_distinct_round_identity(self):
        e = [evidence(AUDIT), evidence(BACKEND, "FAILED", "TIMEOUT", rnd=2), evidence(FRONTEND, rnd=2)]
        m, r = self.matrix(e, rnd=3)
        self.assertEqual([x["task_id"] for x in m["include"]], [BACKEND])
        self.assertEqual(r["tasks"][BACKEND]["attempts"], 1)
        e.append(evidence(BACKEND, "FAILED", "TIMEOUT", rnd=3))
        _, r = self.matrix(e, rnd=4)
        self.assertEqual(r["tasks"][BACKEND]["state"], "FAILED")
        self.assertEqual(r["tasks"][BACKEND]["attempts"], 2)

    def test_done_dag_has_empty_matrix(self):
        e = [evidence(t) for t in (AUDIT, BACKEND, FRONTEND, REPORT)]
        m, r = self.matrix(e, rnd=4)
        self.assertEqual(m["include"], [])
        self.assertEqual(r["outcome"], "COMPLETE")

    def test_matrix_has_no_prompt(self):
        m, _ = self.matrix([evidence(AUDIT)], rnd=2)
        self.assertNotIn("Create playground", json.dumps(m))

    def test_bounds(self):
        for rnd in (0, 7, True, "1"):
            with self.assertRaises(ValueError):
                self.matrix(rnd=rnd)
        for cap in (0, 6, True):
            with self.assertRaises(ValueError):
                self.matrix(capacity=cap)
        for dl in (1, 601, True, None):
            with self.assertRaises(ValueError):
                self.matrix(deadline=dl)

    def test_evidence_from_unfinished_round_rejected(self):
        with self.assertRaises(ValueError):
            self.matrix([evidence(AUDIT, rnd=2)], rnd=2)

    def test_context_must_be_trusted_manual_main(self):
        for key, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/x"),
                           ("GITHUB_REPOSITORY", "evil/repo")):
            env = context()
            env[key] = value
            with self.assertRaises(ValueError):
                self.matrix(env=env)

    def test_forged_or_stale_evidence_rejected(self):
        bad = evidence(AUDIT)
        bad["published"] = True
        with self.assertRaises(ValueError):
            self.matrix([bad], rnd=2)
        with self.assertRaises(ValueError):
            self.matrix([evidence(AUDIT, sha="f" * 40)], rnd=2)
        wrong_round = evidence(AUDIT, rnd=9)
        with self.assertRaises(ValueError):
            self.matrix([wrong_round], rnd=2)


class DagWorkerTests(unittest.TestCase):
    @contextmanager
    def sandbox(self, task_id=BACKEND):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root)
            for cmd in (["init", "-q"], ["config", "user.email", "t@invalid.test"],
                        ["config", "user.name", "Offline Test"]):
                subprocess.run(["git", *cmd], cwd=d, check=True)
            (d / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=d, check=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=d, text=True).strip()
            env = context(sha)
            old = os.getcwd()
            os.chdir(d)
            try:
                temp = d / ".git" / "dag-test"
                dag.prepare(RAW, task_id, env, sha, temp)
                yield d, sha, env, temp
            finally:
                os.chdir(old)

    def test_prepare_binds_manifest_identity(self):
        with self.sandbox() as (d, sha, env, temp):
            ctx = json.loads((temp / "f2-context.json").read_text())
            self.assertEqual((ctx["plan_id"], ctx["task_id"], ctx["issue_number"]),
                             ("f2-dag", BACKEND, 10))
            self.assertEqual(ctx["allowed_paths"], ["playground/f2/dag/backend.txt"])
            self.assertFalse(ctx["publication_allowed"])
            self.assertIn("ONLY the exact file playground/f2/dag/backend.txt",
                          (temp / "f2-prompt.txt").read_text())

    def test_prepare_unknown_task(self):
        with self.assertRaises(ValueError):
            with self.sandbox(task_id="f2-dag-nope"):
                pass

    def test_success_evidence_is_valid_for_scheduler(self):
        with self.sandbox() as (d, sha, env, temp):
            Path("playground/f2/dag").mkdir(parents=True)
            Path("playground/f2/dag/backend.txt").write_text("ok\n", encoding="utf-8")
            ev = dag.finalize(RAW, BACKEND, env, sha, temp, "success", temp / "art",
                              dict(exit_code=0, seconds=5, deadline=300), 2)
            self.assertEqual((ev["worker_outcome"], ev["round"], ev["issue_number"]),
                             ("SUCCEEDED", 2, 10))
            self.assertEqual(ev["allowed_paths"], ["playground/f2/dag/backend.txt"])
            audit = evidence(AUDIT, sha=sha)
            result = sched.schedule(RAW, [audit, ev], sha, capacity=3)
            self.assertEqual(result["tasks"][BACKEND]["state"], "SUCCEEDED")
            self.assertEqual(result["dispatch"], [FRONTEND])
            with self.assertRaises(ValueError):   # success before its dependency
                sched.schedule(RAW, [ev], sha, capacity=3)
            self.assertTrue((temp / "art" / "changes.patch").exists())

    def test_sibling_file_is_scope_violation(self):
        with self.sandbox() as (d, sha, env, temp):
            Path("playground/f2/dag").mkdir(parents=True)
            Path("playground/f2/dag/backend.txt").write_text("ok\n", encoding="utf-8")
            Path("playground/f2/dag/frontend.txt").write_text("not mine\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside exact"):
                dag.finalize(RAW, BACKEND, env, sha, temp, "success", temp / "art",
                             dict(exit_code=0, seconds=5, deadline=300), 1)
            ev = json.loads((temp / "art" / "result.json").read_text())
            self.assertEqual(ev["failure_class"], "SCOPE_VIOLATION")
            self.assertFalse((temp / "art" / "changes.patch").exists())

    def test_invalid_round_rejected(self):
        with self.sandbox() as (d, sha, env, temp):
            for bad in (0, 7, True):
                with self.assertRaises(ValueError):
                    dag.finalize(RAW, BACKEND, env, sha, temp, "config-only", temp / "art",
                                 None, bad)

    def test_pilot_defaults_unchanged(self):
        self.assertEqual(len(pilot.read_manifest(json.loads(
            (ROOT / "examples/ocfilo-f2-execution-pilot.json").read_text(encoding="utf-8-sig")))), 3)


class CapacityPlanTests(unittest.TestCase):
    CAP5 = json.loads((ROOT / "examples/ocfilo-f2-capacity-5.json").read_text(encoding="utf-8-sig"))

    def test_caps_are_registered_per_plan(self):
        self.assertEqual(dag.CAPS, {"f2-dag": 5, "f2-cap5": 5, "f2-cap10": 10})

    def test_capacity_5_manifest_is_five_independent_tasks(self):
        tasks = dag.read_dag(self.CAP5)
        self.assertEqual(len(tasks), 5)
        self.assertTrue(all(t["depends_on"] == [] for t in tasks.values()))

    def test_five_dispatched_and_six_rejected(self):
        m, _ = dag.round_matrix(self.CAP5, [], context(), SHA, 1, 5, 300)
        self.assertEqual(len(m["include"]), 5)
        self.assertEqual(len({x["allowed_path"] for x in m["include"]}), 5)
        with self.assertRaises(ValueError):
            dag.round_matrix(RAW, [], context(), SHA, 1, 6, 300)

    def test_capacity_10_plan_is_the_only_one_allowing_ten(self):
        cap10 = json.loads((ROOT / "examples/ocfilo-f2-capacity-10.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(dag.read_dag(cap10)), 10)
        m, _ = dag.round_matrix(cap10, [], context(), SHA, 1, 10, 300)
        self.assertEqual(len(m["include"]), 10)
        for raw in (RAW, self.CAP5):
            with self.assertRaises(ValueError):
                dag.round_matrix(raw, [], context(), SHA, 1, 10, 300)
        with self.assertRaises(ValueError):
            dag.round_matrix(cap10, [], context(), SHA, 1, 11, 300)

    def test_never_above_plan_cap(self):
        for cap in (6, 10, 0, True):
            with self.assertRaises(ValueError):
                dag.round_matrix(self.CAP5, [], context(), SHA, 1, cap, 300)

    def test_lower_capacity_still_bounds_dispatch(self):
        m, _ = dag.round_matrix(self.CAP5, [], context(), SHA, 1, 2, 300)
        self.assertEqual(len(m["include"]), 2)

    def test_unregistered_plan_id_rejected_even_if_valid_plan(self):
        raw = copy.deepcopy(self.CAP5)
        raw["plan_id"] = "f2-cap9"
        for t in raw["tasks"]:
            t["task_id"] = t["task_id"].replace("f2-cap5", "f2-cap9")
        with self.assertRaises(ValueError):
            dag.read_dag(raw)

    def test_scheduler_cap_parameter_is_bounded(self):
        for bad in (0, 11, True):
            with self.assertRaises(ValueError):
                sched.schedule(self.CAP5, [], SHA, 1, max_capacity=bad)
        with self.assertRaises(ValueError):
            sched.schedule(self.CAP5, [], SHA, 4, max_capacity=3)


class WorkflowContractTests(unittest.TestCase):
    RUN = (ROOT / ".github/workflows/ocfilo-f2-dag-run.yml").read_text(encoding="utf-8")
    ROUND = (ROOT / ".github/workflows/ocfilo-f2-dag-round.yml").read_text(encoding="utf-8")
    ALLOWED_USES = {"actions/checkout@v5", "actions/setup-node@v5",
                    "actions/upload-artifact@v6", "actions/download-artifact@v7",
                    "./.github/workflows/ocfilo-f2-dag-round.yml"}

    def test_triggers_are_manual_only(self):
        for forbidden in ("schedule:", "push:", "pull_request", "workflow_run",
                          "repository_dispatch", "issue_comment"):
            self.assertNotIn(forbidden, self.RUN)
            self.assertNotIn(forbidden, self.ROUND)
        self.assertIn("workflow_dispatch:", self.RUN)
        self.assertNotIn("workflow_dispatch", self.ROUND)
        self.assertIn("workflow_call:", self.ROUND)

    def test_one_round_job_per_allowed_round_and_no_more(self):
        calls = self.RUN.count("uses: ./.github/workflows/ocfilo-f2-dag-round.yml")
        self.assertEqual(calls, pilot.MAX_ROUNDS)
        self.assertIn(f"round: {pilot.MAX_ROUNDS}\n", self.RUN)
        self.assertNotIn(f"round: {pilot.MAX_ROUNDS + 1}\n", self.RUN)

    def test_least_privilege_and_no_secrets(self):
        for text in (self.RUN, self.ROUND):
            self.assertIn("contents: read", text)
            self.assertNotIn("write", text.replace("non-publishing", ""))
            self.assertNotIn("secrets.", text)
            self.assertNotIn("GH_TOKEN", text)
            self.assertNotIn("GITHUB_TOKEN", text)
        self.assertIn("persist-credentials: false", self.ROUND)

    def test_only_approved_pinned_actions(self):
        import re
        for text in (self.RUN, self.ROUND):
            for used in re.findall(r"uses: (\S+)", text):
                self.assertIn(used, self.ALLOWED_USES)

    def test_deadline_cap_and_serialization(self):
        self.assertIn("timeout --signal=TERM --kill-after=20", self.ROUND)
        self.assertIn("max-parallel: 10", self.ROUND)
        self.assertIn("group: ocfilo-f2-dag-run", self.RUN)
        self.assertIn("cancel-in-progress: false", self.RUN)
        self.assertIn("options: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']", self.RUN)
        self.assertIn("options: ['300', '600', '900']", self.RUN)

    def test_artifacts_are_round_scoped_and_matchable(self):
        base = "ocfilo-f2-${{ github.run_id }}-${{ github.run_attempt }}-"
        self.assertIn(base + "r${{ inputs.round }}-${{ matrix.task_id }}", self.ROUND)
        self.assertIn("pattern: " + base + "*", self.ROUND)
        self.assertIn("pattern: " + base + "*", self.RUN)

    def test_complete_required_only_when_executing(self):
        self.assertIn("--require-complete", self.RUN)
        self.assertIn("'execute'", self.RUN)

    def test_later_rounds_run_only_after_cancellation_check(self):
        self.assertEqual(self.RUN.count("!cancelled() && inputs.run_mode == 'execute'"),
                         pilot.MAX_ROUNDS - 1)


class CliTests(unittest.TestCase):
    @contextmanager
    def repo_env(self):
        from unittest.mock import patch
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        old = os.getcwd()
        os.chdir(ROOT)
        try:
            with patch.dict(os.environ, context(head)), tempfile.TemporaryDirectory() as tmp:
                yield head, Path(tmp)
        finally:
            os.chdir(old)

    def test_matrix_op_writes_bounded_outputs(self):
        with self.repo_env() as (head, tmp):
            (tmp / "ev").mkdir()
            out = tmp / "gh_output"
            dag.main(["matrix", "--plan", "dag-pilot", "--round", "1",
                      "--evidence-dir", str(tmp / "ev"), "--out", str(out)])
            lines = dict(x.split("=", 1) for x in out.read_text().splitlines())
            self.assertEqual(lines["count"], "1")
            self.assertEqual(lines["outcome"], "IN_PROGRESS")
            self.assertEqual(json.loads(lines["matrix"])["include"][0]["task_id"], AUDIT)

    def test_unregistered_plan_refused(self):
        with self.repo_env() as (head, tmp), self.assertRaises(SystemExit):
            dag.main(["matrix", "--plan", "nope", "--out", str(tmp / "o")])

    def test_state_require_complete_fails_when_incomplete(self):
        with self.repo_env() as (head, tmp):
            (tmp / "ev").mkdir()
            with self.assertRaises(SystemExit) as cm:
                dag.main(["state", "--plan", "dag-pilot", "--evidence-dir", str(tmp / "ev"),
                          "--out", str(tmp / "state.json"), "--require-complete"])
            self.assertEqual(cm.exception.code, 2)
            self.assertTrue((tmp / "state.json").exists())  # state still recorded


if __name__ == "__main__":
    unittest.main(verbosity=2)
