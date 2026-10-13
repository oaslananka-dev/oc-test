#!/usr/bin/env python3
"""Negative/positive offline tests for the Phase 2 plan-only DAG planner."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from ocfilo_f2_plan import plan, main

REPO = "oaslananka-dev/oc-test"


def fixture(count=3, parallel=3):
    ids = [f"f2-demo-task-{i:02d}" for i in range(count)]
    return {
        "schema_version": 1, "plan_id": "f2-demo",
        "repository": REPO, "run_mode": "plan-only",
        "max_parallel": parallel, "tasks": [{
            "task_id": tid, "prompt": "Implement one scoped fixture update",
            "model": "opencode/big-pickle", "agent": "build",
            "profile": "base", "identity": "actions-readonly",
            "allowed_paths": [f"playground/f2/file-{i:02d}.txt"],
            "depends_on": [],
        } for i, tid in enumerate(ids)],
    }


class Phase2PlannerTests(unittest.TestCase):
    def reject(self, case):
        with self.assertRaises((ValueError, TypeError)):
            plan(case)

    def test_single_task(self):
        p = plan(fixture(1))
        self.assertEqual((p["task_count"], p["wave_count"]), (1, 1))
        self.assertIs(p["dispatch_allowed"], False)
        self.assertIs(p["execution_authorized"], False)

    def test_ten_parallel_plan_only(self):
        p = plan(fixture(10, 10))
        self.assertEqual([w["worker_count"] for w in p["waves"]], [10])
        self.assertEqual(p["task_count"], 10)
        self.assertFalse(p["dispatch_allowed"])

    def test_ten_tasks_bounded_three(self):
        p = plan(fixture(10, 3))
        self.assertEqual([w["worker_count"] for w in p["waves"]], [3, 3, 3, 1])

    def test_dag(self):
        b = fixture(4, 4)
        b["tasks"][2]["depends_on"] = [b["tasks"][0]["task_id"]]
        b["tasks"][3]["depends_on"] = [b["tasks"][1]["task_id"], b["tasks"][2]["task_id"]]
        result = plan(b)
        self.assertEqual([w["worker_count"] for w in result["waves"]], [2, 1, 1])
        self.assertEqual([w["tasks"][0]["task_id"] for w in result["waves"][1:]],
                         ["f2-demo-task-02", "f2-demo-task-03"])

    def test_order_independent_dependencies(self):
        b = fixture(2)
        b["tasks"][0]["depends_on"] = [b["tasks"][1]["task_id"]]
        result = plan(b)
        self.assertEqual(result["waves"][0]["tasks"][0]["task_id"], "f2-demo-task-01")

    def test_deterministic(self):
        a = fixture(10, 4)
        self.assertEqual(plan(a), plan(copy.deepcopy(a)))

    def test_no_prompts_in_summary(self):
        p = plan(fixture())
        self.assertNotIn("Implement one scoped fixture", json.dumps(p))

    def test_invalid_plan_version(self):
        b = fixture(); b["schema_version"] = 2; self.reject(b)

    def test_invalid_bool_version(self):
        b = fixture(); b["schema_version"] = True; self.reject(b)

    def test_unapproved_repository(self):
        b = fixture(); b["repository"] = "another/repo"; self.reject(b)

    def test_execution_mode_rejected(self):
        b = fixture(); b["run_mode"] = "execute"; self.reject(b)

    def test_unknown_root_field(self):
        b = fixture(); b["webhook"] = "any"; self.reject(b)

    def test_zero_tasks(self):
        self.reject(fixture(0))

    def test_eleventh_task(self):
        self.reject(fixture(11))

    def test_zero_parallelism(self):
        b = fixture(); b["max_parallel"] = 0; self.reject(b)

    def test_boolean_parallelism(self):
        b = fixture(); b["max_parallel"] = True; self.reject(b)

    def test_oversubscribed_parallelism(self):
        self.reject(fixture(1, 11))

    def test_duplicate_task_id(self):
        b = fixture(); b["tasks"][1]["task_id"] = b["tasks"][0]["task_id"]; self.reject(b)

    def test_wrong_namespace(self):
        b = fixture(); b["tasks"][0]["task_id"] = "foreign"; self.reject(b)

    def test_missing_dependency(self):
        b = fixture(); b["tasks"][1]["depends_on"] = ["f2-demo-missing"]; self.reject(b)

    def test_self_dependency(self):
        b = fixture(); b["tasks"][0]["depends_on"] = [b["tasks"][0]["task_id"]]; self.reject(b)

    def test_cycle(self):
        b = fixture(2)
        b["tasks"][0]["depends_on"] = [b["tasks"][1]["task_id"]]
        b["tasks"][1]["depends_on"] = [b["tasks"][0]["task_id"]]
        self.reject(b)

    def test_duplicate_dependency(self):
        b = fixture()
        b["tasks"][1]["depends_on"] = [b["tasks"][0]["task_id"]] * 2
        self.reject(b)

    def test_exact_path_collision(self):
        b = fixture()
        b["tasks"][1]["allowed_paths"] = b["tasks"][0]["allowed_paths"][:]
        self.reject(b)

    def test_case_insensitive_collision(self):
        b = fixture()
        b["tasks"][1]["allowed_paths"] = [b["tasks"][0]["allowed_paths"][0].upper().replace("PLAYGROUND/", "playground/")]
        self.reject(b)

    def test_traversal(self):
        b = fixture(); b["tasks"][0]["allowed_paths"] = ["playground/../secrets.txt"]; self.reject(b)

    def test_outside_file_scope(self):
        b = fixture(); b["tasks"][0]["allowed_paths"] = [".github/workflows/oc.yml"]; self.reject(b)

    def test_hidden_env_path(self):
        b = fixture(); b["tasks"][0]["allowed_paths"] = ["playground/.env"]; self.reject(b)

    def test_reserved_windows_name(self):
        b = fixture(); b["tasks"][0]["allowed_paths"] = ["playground/CON/secrets.txt"]; self.reject(b)

    def test_unapproved_model(self):
        b = fixture(); b["tasks"][0]["model"] = "paid/provider"; self.reject(b)

    def test_unapproved_agent(self):
        b = fixture(); b["tasks"][0]["agent"] = "sudo"; self.reject(b)

    def test_unapproved_plugin(self):
        b = fixture(); b["tasks"][0]["plugins"] = ["evil"]; self.reject(b)

    def test_unapproved_mcp(self):
        b = fixture(); b["tasks"][0]["mcp"] = {"servers": {"proxy": {}}}; self.reject(b)

    def test_unapproved_identity(self):
        b = fixture(); b["tasks"][0]["identity"] = "personal-pat"; self.reject(b)

    def test_unapproved_tool_profile(self):
        b = fixture(); b["tasks"][0]["profile"] = "plugin-proof"; self.reject(b)

    def test_publish_field_rejected(self):
        b = fixture(); b["tasks"][0]["publish"] = True; self.reject(b)

    def test_issue_reference(self):
        b = fixture(); b["tasks"][0]["issue_number"] = 6
        self.assertEqual(plan(b)["waves"][0]["tasks"][0]["issue_number"], 6)

    def test_invalid_issue_reference(self):
        b = fixture(); b["tasks"][0]["issue_number"] = True; self.reject(b)

    def test_blank_prompt(self):
        b = fixture(); b["tasks"][0]["prompt"] = "     "; self.reject(b)

    def test_nul_prompt(self):
        b = fixture(); b["tasks"][0]["prompt"] = "null\x00 injection"; self.reject(b)

    def test_large_prompt(self):
        b = fixture(); b["tasks"][0]["prompt"] = "a" * 3001; self.reject(b)

    def test_directory_instead_of_file(self):
        b = fixture(); b["tasks"][0]["allowed_paths"] = ["playground/f2/directory"]; self.reject(b)

    def test_nested_profile_allowed(self):
        b = fixture(); b["tasks"][0]["profile"] = "runner-check"
        self.assertEqual(plan(b)["waves"][0]["tasks"][0]["profile"], "runner-check")


if __name__ == "__main__":
    unittest.main(verbosity=2)


