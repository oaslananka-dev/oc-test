"""Negative-first offline checks for explicit external PR reconciliation."""
import copy
import unittest
from unittest.mock import patch

import oc_fleet_external_finalize as f

PROJECT = "0506e0e5-01a7-4de9-b63b-67e993809721"
SOURCE = "a" * 40
HEAD = "b" * 40
MERGED = "c" * 40
KEY = "OAS-39"
REPO = "oaslananka/fovux-kit"
WORKER = "MakerParsDev/oc-test"
RUN = "40048027569"
PR = "278"
PATHS = ["fovux-studio/package.json", "fovux-studio/pnpm-lock.yaml"]
META = {
    "fleet_target_repo": REPO,
    "fleet_target_issue": "277",
    "fleet_worker_repo": WORKER,
    "fleet_worker_account": "MakerParsDev",
    "fleet_target_account": "oaslananka",
    "fleet_allowed_path": PATHS[0],
    "fleet_allowed_paths": [PATHS[1]],
    "fleet_ready": False,
    "fleet_result": "pr_created",
    "fleet_run_id": RUN,
    "fleet_pull_request": PR,
    "fleet_agent": "security-review",
    "fleet_model": "opencode/big-pickle",
    "fleet_profile": "security-semgrep",
    "fleet_external_pr_only": True,
    "fleet_auto_merge": False,
}
ISSUE = {
    "identifier": KEY, "project_id": PROJECT, "status": "in_review",
    "assignee_type": None, "metadata": META,
}
MANIFEST = {
    "checkout_sha": SOURCE, "base_branch": "main", "multica_issue": KEY,
    "task_id": "gh-277-f0988caa93b2",
}
REQUIRED = ["ci-required", "security-required", "dependency-review", "codeql-required"]


def fixture(merged=True):
    return {
        f"repos/{WORKER}/actions/runs/{RUN}": {
            "id": int(RUN), "repository": {"full_name": WORKER},
            "event": "workflow_dispatch", "head_branch": "main",
            "path": ".github/workflows/oc-fleet-worker.yml@main",
            "run_attempt": 1, "status": "completed", "conclusion": "success",
        },
        f"repos/{REPO}/pulls/{PR}": {
            "number": int(PR), "state": "closed" if merged else "open",
            "merged": merged, "merged_at": "2026-10-24T05:00:00Z" if merged else None,
            "merge_commit_sha": MERGED if merged else None,
            "head": {"sha": HEAD, "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
            "body": "Closes #277",
        },
        f"repos/{REPO}/pulls/{PR}/commits?per_page=100": [
            {"sha": HEAD},
        ],
        f"repos/{REPO}/git/commits/{HEAD}": {
            "parents": [{"sha": SOURCE}],
        },
        f"repos/{REPO}/pulls/{PR}/files?per_page=100": [
            {"filename": p, "status": "modified"} for p in PATHS
        ],
        f"repos/{REPO}/rules/branches/main?per_page=100": [{
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": n} for n in REQUIRED]},
        }],
        f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100": {
            "total_count": 5,
            "check_runs": [
                {"name": n, "head_sha": HEAD, "status": "completed",
                 "conclusion": "success"} for n in REQUIRED
            ] + [{"name": "other optional", "head_sha": HEAD,
                  "status": "completed", "conclusion": "skipped"}],
        },
        f"repos/{REPO}/commits/{HEAD}/status?per_page=100": {
            "total_count": 0, "statuses": [], "state": "pending",
        },
        f"repos/{REPO}/issues/277": {"number": 277, "state": "closed"},
    }


class ExternalFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.db = fixture()
        self.issue = copy.deepcopy(ISSUE)
        self.db_api = patch.object(
            f, "api", side_effect=lambda route: copy.deepcopy(self.db[route]))
        self.issue_get = patch.object(f.handoff, "read_issue", side_effect=lambda key: self.issue)
        self.receipt = patch.object(f, "original_receipt", return_value=MANIFEST)
        self.db_api.start()
        self.issue_get.start()
        self.receipt.start()
        self.addCleanup(self.db_api.stop)
        self.addCleanup(self.issue_get.stop)
        self.addCleanup(self.receipt.stop)

    def ready(self):
        return f.review(KEY, PROJECT)

    def test_exact_end_to_end_merged_evidence_becomes_ready_not_mutated(self):
        with patch.object(f, "command") as subprocess:
            result = self.ready()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["issue"], KEY)
        self.assertEqual(result["merge_sha"], MERGED)
        self.assertEqual(result["verified_checks"], sorted(REQUIRED))
        subprocess.assert_not_called()

    def test_unmerged_pr_is_waiting_even_with_failed_checks(self):
        self.db = fixture(False)
        self.db[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100"]["check_runs"][0]["conclusion"] = "failure"
        self.assertEqual(self.ready()["state"], "waiting")

    def test_closed_unmerged_pr_refused(self):
        self.db = fixture(False)
        self.db[f"repos/{REPO}/pulls/{PR}"]["state"] = "closed"
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.ready()

    def test_original_worker_fails_or_run_identity_does_not_match(self):
        path = f"repos/{WORKER}/actions/runs/{RUN}"
        for key, value in (("conclusion", "failure"), ("status", "in_progress"),
                           ("run_attempt", 2), ("event", "push"),
                           ("head_branch", "other"), ("id", 1),
                           ("path", ".github/workflows/other.yml@main")):
            with self.subTest(key=key):
                self.db = fixture()
                self.db[path][key] = value
                with self.assertRaisesRegex(ValueError, "Worker execution"):
                    self.ready()

    def test_wrong_task_project_scope_role_or_auth_flags(self):
        for change in (
            {"fleet_ready": True},
            {"fleet_auto_merge": True},
            {"fleet_external_pr_only": False},
            {"fleet_target_repo": "oaslananka-dev/oc-test"},
            {"fleet_agent": "unknown"},
            {"fleet_model": "openai/paid"},
            {"fleet_allowed_path": ".github/workflows/ci.yml"},
            {"fleet_target_issue": "000277"},
        ):
            with self.subTest(change=change):
                self.issue = {**ISSUE, "metadata": {**META, **change}}
                with self.assertRaises((ValueError, KeyError)):
                    self.ready()
        self.issue = {**ISSUE, "project_id": "b" * 36}
        with self.assertRaisesRegex(ValueError, "identity"):
            self.ready()

    def test_wrong_pr_linkage_or_merged_provenance(self):
        path = f"repos/{REPO}/pulls/{PR}"
        for update in (
            {"number": 270}, {"base": {"ref": "dev", "repo": {"full_name": REPO}}},
            {"head": {"sha": HEAD, "repo": {"full_name": "wrong/repo"}}},
            {"body": "Closes #276"}, {"merged_at": None},
            {"merge_commit_sha": None},
        ):
            with self.subTest(update=update):
                self.db = fixture()
                self.db[path].update(update)
                with self.assertRaises(ValueError):
                    self.ready()

    def test_wrong_head_commit_count_or_checkout_ancestry(self):
        for route, value in (
            (f"repos/{REPO}/pulls/{PR}/commits?per_page=100", []),
            (f"repos/{REPO}/pulls/{PR}/commits?per_page=100", [{"sha": "z"*40}]),
            (f"repos/{REPO}/git/commits/{HEAD}", {"parents": [{"sha": "c"*40}]}),
            (f"repos/{REPO}/git/commits/{HEAD}", {"parents": [{"sha": SOURCE}, {"sha": HEAD}]}),
        ):
            with self.subTest(route=route, value=value):
                self.db = fixture()
                self.db[route] = value
                with self.assertRaises(ValueError):
                    self.ready()

    def test_foreign_path_deleted_file_and_truncated_inventory(self):
        path = f"repos/{REPO}/pulls/{PR}/files?per_page=100"
        for files in (
            [{"filename": ".github/workflows/ci.yml", "status": "modified"}],
            [{"filename": PATHS[0], "status": "removed"}],
            [{"filename": PATHS[0], "status": "modified"}] * 100,
            [],
        ):
            with self.subTest(files=files[:1]):
                self.db = fixture()
                self.db[path] = files
                with self.assertRaisesRegex(ValueError, "PR"):
                    self.ready()

    def test_rules_absent_or_required_missing_or_unverified(self):
        rules = f"repos/{REPO}/rules/branches/main?per_page=100"
        for value in ([], [None], [{"type": "required_status_checks",
                "parameters": {"required_status_checks": []}}]):
            with self.subTest(value=value):
                self.db = fixture()
                self.db[rules] = value
                with self.assertRaises((ValueError, TypeError)):
                    self.ready()
        checks = f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100"
        for outcome in ("failure", "neutral", "skipped", "cancelled", "timed_out"):
            with self.subTest(outcome=outcome):
                self.db = fixture()
                self.db[checks]["check_runs"][0]["conclusion"] = outcome
                with self.assertRaises(ValueError):
                    self.ready()

    def test_optional_security_failure_and_incomplete_run_fail_closed(self):
        path = f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100"
        for mutate in (
            lambda checks: checks["check_runs"][-1].update(conclusion="failure"),
            lambda checks: checks["check_runs"][-1].update(status="in_progress"),
            lambda checks: checks.update(total_count=120),
            lambda checks: checks["check_runs"][-1].update(name="ci-required"),
            lambda checks: checks["check_runs"][0].update(head_sha="c"*40),
        ):
            self.db = fixture()
            mutate(self.db[path])
            with self.assertRaises(ValueError):
                self.ready()

    def test_commit_status_fail_closed_and_incomplete_inventory(self):
        path = f"repos/{REPO}/commits/{HEAD}/status?per_page=100"
        for value in (
            {"state": "failure", "total_count": 1,
             "statuses": [{"context": "other", "state": "failure"}]},
            {"state": "pending", "total_count": 1,
             "statuses": [{"context": "other", "state": "pending"}]},
            {"state": "success", "total_count": 2,
             "statuses": [{"context": "other", "state": "success"}]},
        ):
            with self.subTest(value=value):
                self.db = fixture()
                self.db[path] = value
                with self.assertRaises(ValueError):
                    self.ready()

    def test_closed_target_issue_required(self):
        self.db[f"repos/{REPO}/issues/277"]["state"] = "open"
        with self.assertRaisesRegex(ValueError, "not closed"):
            self.ready()

    def test_missing_github_evidence_never_defaults_to_success(self):
        del self.db[f"repos/{REPO}/rules/branches/main?per_page=100"]
        with self.assertRaises(KeyError):
            self.ready()

    def test_done_is_idempotent_only_for_exact_same_sha(self):
        self.issue = copy.deepcopy(ISSUE)
        self.issue["status"] = "done"
        self.issue["metadata"] = {**META, "fleet_result": "completed",
                                  "fleet_merge_sha": MERGED}
        self.assertEqual(self.ready()["state"], "already_completed")
        self.issue["metadata"]["fleet_merge_sha"] = "d"*40
        with self.assertRaisesRegex(ValueError, "Done state"):
            self.ready()

    def test_partial_multica_state_remains_blocked(self):
        self.issue = copy.deepcopy(ISSUE)
        self.issue["metadata"] = {**META, "fleet_result": "completed"}
        with self.assertRaisesRegex(ValueError, "Conflicting intermediate"):
            self.ready()

    def test_apply_never_merges_and_requires_confirmed_result(self):
        result = self.ready()
        with patch.object(f, "command", return_value="") as command:
            for wrong in (("OAS-31", MERGED), (KEY, "d"*40)):
                with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                    f.apply(result, *wrong)
            command.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Post-write"):
                f.apply(result, KEY, MERGED)
            self.assertEqual(command.call_count, 3)
            self.issue = {**ISSUE, "status": "done",
                          "metadata": {**META, "fleet_result": "completed",
                                       "fleet_merge_sha": MERGED}}
            self.assertEqual(f.apply(result, KEY, MERGED), "done")
            self.assertEqual(command.call_count, 6)
            self.assertTrue(all(c.args[:2] == ("multica", "issue")
                                for c in command.call_args_list))
            self.assertNotIn("gh", str(command.call_args_list))

    def test_apply_waiting_or_done_never_writes(self):
        with patch.object(f, "command") as command:
            for state in ("waiting", "already_completed", "blocked"):
                with self.assertRaises(ValueError):
                    f.apply({"state": state, "issue": KEY,
                             "merge_sha": MERGED}, KEY, MERGED)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
