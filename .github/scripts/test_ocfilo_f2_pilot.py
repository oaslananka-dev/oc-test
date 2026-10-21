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

    def test_new_executable_script_is_normalized_before_evidence(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            target = Path("playground/f2/alpha.txt")
            target.parent.mkdir(parents=True)
            target.write_text("#!/usr/bin/env python3\nprint('ok')\n",
                              encoding="utf-8")
            target.chmod(0o755)
            evidence = pilot.finalize(
                SOURCE, "f2-exec-alpha", env, sha, temp, "success",
                temp / "artifact", RUN_OK)
            patch = (temp / "artifact" / "changes.patch").read_bytes()
            self.assertTrue(evidence["scope_verified"])
            self.assertIn(b"new file mode 100644\n", patch)
            self.assertNotIn(b"new file mode 100755", patch)
            self.assertFalse(target.stat().st_mode & 0o111)

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


RUN_OK = dict(exit_code=0, seconds=42, deadline=600)


class FailureTaxonomyTests(unittest.TestCase):
    def classify(self, outcome="failure", log="", **run):
        return pilot.classify_failure(outcome, run.get("exit_code"), run.get("seconds"),
                                      run.get("deadline"), log)

    def test_timeout_exit_code(self):
        self.assertEqual(self.classify(exit_code=124, seconds=600, deadline=600), "TIMEOUT")

    def test_sigkill_only_timeout_at_deadline(self):
        self.assertEqual(self.classify(exit_code=137, seconds=620, deadline=600), "TIMEOUT")
        self.assertEqual(self.classify(exit_code=137, seconds=5, deadline=600), "AGENT_ERROR")

    def test_provider_log_classes(self):
        cases = {
            "Error: 429 Too Many Requests": "RATE_LIMITED",
            "rate limit exceeded, retry later": "RATE_LIMITED",
            "HTTP 503 Service Unavailable": "PROVIDER_ERROR",
            "upstream bad gateway": "PROVIDER_ERROR",
            "read ECONNRESET": "NETWORK",
            "getaddrinfo EAI_AGAIN opencode.ai": "NETWORK",
            "insufficient credits for this model": "QUOTA_EXHAUSTED",
            "status 429: monthly quota exceeded": "QUOTA_EXHAUSTED",
        }
        for log, expected in cases.items():
            self.assertEqual(self.classify(log=log, exit_code=1, seconds=3, deadline=600),
                             expected, log)

    def test_benign_numbers_are_not_provider_errors(self):
        self.assertEqual(self.classify(log="wrote 502 lines in 429 ms", exit_code=1,
                                       seconds=3, deadline=600), "AGENT_ERROR")

    def test_cancelled_and_skipped(self):
        self.assertEqual(self.classify("cancelled"), "CANCELLED")
        self.assertEqual(self.classify("skipped"), "NOT_STARTED")

    def test_retryable_is_advisory_subset(self):
        for cls in ("TIMEOUT", "RATE_LIMITED", "PROVIDER_ERROR", "NETWORK"):
            self.assertIn(cls, pilot.RETRYABLE)
        for cls in ("QUOTA_EXHAUSTED", "SCOPE_VIOLATION", "CONFIG_TAMPERED", "CANCELLED"):
            self.assertNotIn(cls, pilot.RETRYABLE)

    def test_deadline_allow_list(self):
        for d in (300, 600, 900):
            m = pilot.make_matrix(SOURCE, 3, context(), SHA, deadline=d)
            self.assertEqual({x["deadline_seconds"] for x in m["include"]}, {d})
        for d in (0, 1, 601, 3600, True, "600", None):
            with self.assertRaises(ValueError):
                pilot.make_matrix(SOURCE, 1, context(), SHA, deadline=d)

    def test_agent_run_record_validation(self):
        self.assertEqual(pilot.validate_agent_run(None),
                         dict(exit_code=None, seconds=None, deadline=None))
        for bad in (dict(exit_code=True, seconds=1, deadline=600),
                    dict(exit_code=256, seconds=1, deadline=600),
                    dict(exit_code=0, seconds=-1, deadline=600),
                    dict(exit_code=0, seconds=1, deadline=7),
                    dict(exit_code=0, seconds=10**9, deadline=600)):
            with self.assertRaises(ValueError):
                pilot.validate_agent_run(bad)


class FailureEvidenceTests(unittest.TestCase):
    make_sandbox = PilotContractTests.make_sandbox

    def fail(self, outcome, run, log=None, extra=None):
        with self.make_sandbox() as (directory, sha, env, temp):
            if log is not None:
                (temp / "f2-agent.log").write_text(log, encoding="utf-8")
            if extra:
                extra()
            with self.assertRaises(ValueError):
                pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp, outcome,
                               temp / "artifact", run)
            files = sorted(p.name for p in (temp / "artifact").iterdir())
            self.assertEqual(files, ["result.json"])
            text = (temp / "artifact" / "result.json").read_text()
            return json.loads(text), text

    def test_success_evidence_contract(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            target = Path("playground/f2/alpha.txt")
            target.parent.mkdir(parents=True)
            target.write_text("safe alpha\n", encoding="utf-8")
            ev = pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp, "success",
                                temp / "artifact", RUN_OK)
            self.assertEqual(ev["schema_version"], 2)
            self.assertEqual(ev["worker_outcome"], "SUCCEEDED")
            self.assertIsNone(ev["failure_class"])
            self.assertFalse(ev["retryable"])
            self.assertEqual((ev["agent_exit_code"], ev["agent_seconds"], ev["deadline_seconds"]),
                             (0, 42, 600))
            self.assertEqual(ev["idempotency_key"], pilot.idempotency_key(sha, "f2-exec-alpha"))
            pilot.validate_evidence(ev)

    def test_config_only_has_no_agent_fields(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            ev = pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp, "config-only",
                                temp / "artifact", RUN_OK)
            self.assertEqual(ev["worker_outcome"], "CONFIG_ONLY")
            self.assertIsNone(ev["agent_exit_code"])
            self.assertIsNone(ev["deadline_seconds"])
            pilot.validate_evidence(ev)

    def test_timeout_is_not_reported_as_no_change(self):
        ev, _ = self.fail("failure", dict(exit_code=124, seconds=600, deadline=600))
        self.assertEqual(ev["worker_outcome"], "FAILED")
        self.assertEqual(ev["failure_class"], "TIMEOUT")
        self.assertTrue(ev["retryable"])
        self.assertFalse(ev["scope_verified"])
        pilot.validate_evidence(ev)

    def test_provider_class_from_log_and_log_not_published(self):
        ev, text = self.fail("failure", dict(exit_code=1, seconds=3, deadline=600),
                             log="secret-marker 429 Too Many Requests")
        self.assertEqual(ev["failure_class"], "RATE_LIMITED")
        self.assertNotIn("secret-marker", text)
        pilot.validate_evidence(ev)

    def test_quota_not_retryable(self):
        ev, _ = self.fail("failure", dict(exit_code=1, seconds=3, deadline=600),
                          log="insufficient credits")
        self.assertEqual(ev["failure_class"], "QUOTA_EXHAUSTED")
        self.assertFalse(ev["retryable"])

    def test_cancelled_outcome(self):
        ev, _ = self.fail("cancelled", None)
        self.assertEqual(ev["worker_outcome"], "CANCELLED")
        self.assertEqual(ev["failure_class"], "CANCELLED")
        pilot.validate_evidence(ev)

    def test_skipped_agent_means_not_started(self):
        ev, _ = self.fail("skipped", None)
        self.assertEqual(ev["failure_class"], "NOT_STARTED")

    def test_success_without_change_is_no_change(self):
        ev, _ = self.fail("success", RUN_OK)
        self.assertEqual(ev["failure_class"], "NO_CHANGE")

    def test_failed_agent_scope_escape_still_flagged(self):
        def escape():
            Path("outside.txt").write_text("x\n", encoding="utf-8")
        ev, _ = self.fail("failure", dict(exit_code=124, seconds=600, deadline=600), extra=escape)
        self.assertEqual(ev["failure_class"], "SCOPE_VIOLATION")
        self.assertFalse(ev["retryable"])

    def test_config_tamper_class(self):
        def tamper():
            Path("playground/f2").mkdir(parents=True)
            Path("playground/f2/alpha.txt").write_text("a\n", encoding="utf-8")
            Path("opencode.json").write_text('{"model": "paid/model"}')
        ev, _ = self.fail("success", RUN_OK, extra=tamper)
        self.assertEqual(ev["failure_class"], "CONFIG_TAMPERED")

    def test_invalid_agent_record_fails_closed(self):
        ev, _ = self.fail("success", dict(exit_code=True, seconds=1, deadline=600))
        self.assertEqual(ev["worker_outcome"], "FAILED")
        self.assertEqual(ev["failure_class"], "EVIDENCE_INVALID")

    def test_validate_evidence_rejects_tampering(self):
        with self.make_sandbox() as (directory, sha, env, temp):
            Path("playground/f2").mkdir(parents=True)
            Path("playground/f2/alpha.txt").write_text("a\n", encoding="utf-8")
            good = pilot.finalize(SOURCE, "f2-exec-alpha", env, sha, temp, "success",
                                  temp / "artifact", RUN_OK)
        mutations = {
            "published": lambda e: e.update(published=True),
            "success-with-class": lambda e: e.update(failure_class="TIMEOUT"),
            "success-unverified": lambda e: e.update(scope_verified=False),
            "wrong-key": lambda e: e.update(idempotency_key="0" * 64),
            "unknown-outcome": lambda e: e.update(worker_outcome="RUNNING"),
            "unknown-class": lambda e: e.update(worker_outcome="FAILED", failure_class="WHATEVER",
                                                scope_verified=False),
            "failed-verified": lambda e: e.update(worker_outcome="FAILED", failure_class="TIMEOUT"),
            "wrong-repo-id": lambda e: e.update(repository_id=1),
            "bool-seconds": lambda e: e.update(agent_seconds=True),
            "missing-key": lambda e: e.pop("idempotency_key"),
        }
        for name, mutate in mutations.items():
            evidence = copy.deepcopy(good)
            mutate(evidence)
            with self.assertRaises(ValueError, msg=name):
                pilot.validate_evidence(evidence)


class WorkflowContractTests(unittest.TestCase):
    TEXT = (ROOT / ".github/workflows/ocfilo-f2-worker-pilot.yml").read_text(encoding="utf-8")

    def test_only_manual_dispatch(self):
        for forbidden in ("schedule:", "push:", "pull_request", "workflow_run", "repository_dispatch"):
            self.assertNotIn(forbidden, self.TEXT)
        self.assertIn("workflow_dispatch:", self.TEXT)

    def test_agent_runs_under_hard_deadline(self):
        self.assertIn("timeout --signal=TERM --kill-after=20", self.TEXT)
        self.assertIn("agent_deadline_seconds", self.TEXT)
        self.assertIn("F2_DEADLINE_SECONDS", self.TEXT)

    def test_artifact_name_unique_per_attempt_and_task(self):
        self.assertIn("ocfilo-f2-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.task_id }}",
                      self.TEXT)

    def test_concurrency_cap_and_permissions_unchanged(self):
        self.assertIn("max-parallel: 3", self.TEXT)
        self.assertIn("contents: read", self.TEXT)
        self.assertIn("persist-credentials: false", self.TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
