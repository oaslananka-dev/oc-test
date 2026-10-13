#!/usr/bin/env python3
"""F2.2 human-dispatched only worker identity, scope and evidence tests."""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ocfilo_f2_pilot as pilot

ROOT = Path(__file__).resolve().parents[2]
SOURCE = json.loads((ROOT / "examples/ocfilo-f2-execution-pilot.json").read_text(encoding="utf-8-sig"))
SHA = "a" * 40


def context(sha=SHA):
    return {"GITHUB_REPOSITORY": pilot.REPO,
            "GITHUB_REPOSITORY_ID": pilot.REPO_ID,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": sha, "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1"}


class PilotContractTests(unittest.TestCase):
    def reject(self, source):
        with self.assertRaises((ValueError, TypeError, KeyError)):
            pilot.read_manifest(source)

    def test_fixed_manifest_valid(self):
        self.assertEqual(len(pilot.read_manifest(SOURCE)), 3)

    def test_only_1_2_3_workers(self):
        for n in (1, 2, 3):
            self.assertEqual(len(pilot.make_matrix(SOURCE, n, context(), SHA)["include"]), n)
        for n in (0, 4, True, "3"):
            with self.assertRaises(ValueError):
                pilot.make_matrix(SOURCE, n, context(), SHA)

    def test_matrix_contains_no_prompt_or_secret(self):
        j = json.dumps(pilot.make_matrix(SOURCE, 3, context(), SHA))
        self.assertNotIn("Create playground", j)
        self.assertNotIn("password", j)

    def test_parallel_tasks_have_unique_paths(self):
        m = pilot.make_matrix(SOURCE, 3, context(), SHA)
        self.assertEqual(len({x["allowed_path"] for x in m["include"]}), 3)

    def test_wrong_repo(self):
        e = context(); e["GITHUB_REPOSITORY"] = "attacker/repo"
        with self.assertRaises(ValueError):
            pilot.make_matrix(SOURCE, 1, e, SHA)

    def test_wrong_repository_id(self):
        e = context(); e["GITHUB_REPOSITORY_ID"] = "123"
        with self.assertRaises(ValueError):
            pilot.make_matrix(SOURCE, 1, e, SHA)

    def test_wrong_event_ref(self):
        e = context(); e["GITHUB_REF"] = "refs/heads/feature"
        with self.assertRaises(ValueError):
            pilot.make_matrix(SOURCE, 1, e, SHA)

    def test_automatic_event_denied(self):
        e = context(); e["GITHUB_EVENT_NAME"] = "push"
        with self.assertRaises(ValueError):
            pilot.make_matrix(SOURCE, 1, e, SHA)

    def test_wrong_sha_denied(self):
        with self.assertRaises(ValueError):
            pilot.make_matrix(SOURCE, 1, context(), "b"*40)

    def test_invalid_run_identity(self):
        e = context(); e["GITHUB_RUN_ID"] = "not-an-id"
        with self.assertRaises(ValueError):
            pilot.make_matrix(SOURCE, 1, e, SHA)

    def test_wrong_scope(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][0]["allowed_paths"] = ["playground/f2/gamma.txt"]
        self.reject(b)

    def test_unapproved_mcp_plugin(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][0]["mcp"] = {"untrusted": {}}
        self.reject(b)

    def test_unapproved_model(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][0]["model"] = "paid/model"
        self.reject(b)

    def test_unapproved_identity(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][0]["identity"] = "my-personal-github"
        self.reject(b)

    def test_dependencies_not_executed_before_proof(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][1]["depends_on"] = [b["tasks"][0]["task_id"]]
        self.reject(b)

    def test_wrong_issue(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][0]["issue_number"] = 6
        self.reject(b)

    def test_extra_worker(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"].append(copy.deepcopy(b["tasks"][0]))
        self.reject(b)

    def test_duplicate_path(self):
        b = copy.deepcopy(SOURCE)
        b["tasks"][1]["allowed_paths"] = b["tasks"][0]["allowed_paths"][:]
        self.reject(b)

    def test_one_output_file_can_be_accepted(self):
        with tempfile.TemporaryDirectory() as base:
            directory = Path(base)
            subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
            subprocess.run(["git", "config", "user.email", "local@invalid.test"], cwd=directory, check=True)
            subprocess.run(["git", "config", "user.name", "Offline Test"], cwd=directory, check=True)
            (directory / "README.md").write_text("local test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=directory, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=directory, check=True)
            sha = subprocess.check_output(["git","rev-parse","HEAD"], cwd=directory, text=True).strip()
            env = context(sha)
            temp = directory / ".git" / "f2-test-temp"
            with patch.dict(os.environ,env):
                old = Path.cwd()
                os.chdir(directory)
                try:
                    pilot.prepare(SOURCE, "f2-exec-alpha", env, sha, temp)
                    out = Path("playground/f2/alpha.txt")
                    out.parent.mkdir(parents=True)
                    out.write_text("alpha output\n", encoding="utf-8")
                    patch_bytes, evidence = pilot.evaluate_worktree("f2-exec-alpha", env, temp, "success")
                    self.assertIn(b"alpha output", patch_bytes)
                    self.assertEqual(evidence["task_id"], "f2-exec-alpha")
                finally:
                    os.chdir(old)


    def make_sandbox(self):
        from contextlib import contextmanager

        @contextmanager
        def create():
            with tempfile.TemporaryDirectory() as root:
                directory = Path(root)
                subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
                subprocess.run(["git", "config", "user.email", "local@invalid.test"], cwd=directory, check=True)
                subprocess.run(["git", "config", "user.name", "Offline Test"], cwd=directory, check=True)
                (directory / "README.md").write_text("fixture\n", encoding="utf-8")
                subprocess.run(["git", "add", "README.md"], cwd=directory, check=True)
                subprocess.run(["git", "commit", "-qm", "fixture"], cwd=directory, check=True)
                sha = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                              cwd=directory, text=True).strip()
                env = context(sha)
                old = os.getcwd()
                os.chdir(directory)
                try:
                    temp = directory / ".git" / "f2-test"
                    pilot.prepare(SOURCE, "f2-exec-alpha", env, sha, temp)
                    yield directory, sha, env, temp
                finally:
                    os.chdir(old)
        return create()

    def test_offline_output_result_evidence(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            target = Path("playground/f2/alpha.txt")
            target.parent.mkdir(parents=True)
            target.write_text("safe alpha\n", encoding="utf-8")
            result = pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp,
                                    "success", temp / "artifact")
            self.assertEqual(result["worker_outcome"], "SUCCEEDED")
            self.assertTrue(result["scope_verified"])
            self.assertFalse(result["published"])
            self.assertEqual(result["allowed_paths"], ["playground/f2/alpha.txt"])
            self.assertEqual(result["repository_id"], 1416228069)
            self.assertEqual(result["issue_number"], 10)
            self.assertTrue((temp / "artifact" / "changes.patch").exists())

    def test_scope_escape_rejected(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            target = Path("playground/f2/alpha.txt")
            target.parent.mkdir(parents=True)
            target.write_text("alpha\n", encoding="utf-8")
            Path("outside.txt").write_text("unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside exact"):
                pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp,
                               "success", temp / "artifact")
            evidence = json.loads((temp / "artifact" / "result.json").read_text())
            self.assertFalse(evidence["scope_verified"])
            self.assertFalse((temp / "artifact" / "changes.patch").exists())

    def test_modified_opencode_config_rejected(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            target = Path("playground/f2/alpha.txt")
            target.parent.mkdir(parents=True)
            target.write_text("alpha\n", encoding="utf-8")
            Path("opencode.json").write_text('{"model": "paid/model"}')
            with self.assertRaisesRegex(ValueError, "configuration"):
                pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp,
                               "success", temp / "artifact")

    def test_agent_failure_does_not_validate_result(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            target = Path("playground/f2/alpha.txt")
            target.parent.mkdir(parents=True)
            target.write_text("alpha\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "successfully"):
                pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp,
                               "failure", temp / "artifact")
            evidence = json.loads((temp / "artifact" / "result.json").read_text())
            self.assertEqual(evidence["worker_outcome"], "FAILED")
            self.assertFalse(evidence["published"])

    def test_failure_outcome_never_accepted(self):
        with self.assertRaises((ValueError, OSError)):
            pilot.evaluate_worktree("f2-exec-alpha", context(), "folder-not-found", "failure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
