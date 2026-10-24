"""Offline negative-first terminal supersession tests; never invoke real CLIs."""
import copy
import unittest
from unittest.mock import patch

import oc_fleet_external_supersede as s
import oc_fleet_external_finalize as f
from test_oc_fleet_external_finalize import (
    fixture, ISSUE, META, MANIFEST, KEY, PROJECT, REPO, PR, HEAD,
    SOURCE, PATHS, REQUIRED, WORKER, RUN,
)

REPLACEMENT = "281"
NEW_HEAD = "d" * 40
MERGE = "e" * 40
MAIN = "f" * 40


def db_fixture():
    db = fixture(False)
    original = db[f"repos/{REPO}/pulls/{PR}"]
    original["state"] = "closed"
    original["closed_at"] = "2026-10-24T13:05:04Z"
    original["merged_at"] = None
    original["merge_commit_sha"] = None
    db[f"repos/{REPO}/pulls/{REPLACEMENT}"] = {
        "number": int(REPLACEMENT), "state": "closed", "merged": True,
        "merged_at": "2026-10-24T13:00:14Z",
        "merge_commit_sha": MERGE,
        "head": {"sha": NEW_HEAD, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "body": "Relates to #277",
    }
    db[f"repos/{REPO}/pulls/{REPLACEMENT}/files?per_page=100"] = [
        {"filename": x, "status": "modified"} for x in PATHS
    ] + [{"filename": "fovux-mcp/uv.lock", "status": "modified"}]
    db[f"repos/{REPO}/commits/{NEW_HEAD}/check-runs?per_page=100"] = {
        "total_count": len(REQUIRED) + 1, "check_runs": [
            {"name": n, "head_sha": NEW_HEAD, "status": "completed",
             "conclusion": "success"} for n in REQUIRED
        ] + [{"name": "optional skipped", "head_sha": NEW_HEAD,
              "status": "completed", "conclusion": "skipped"}],
    }
    db[f"repos/{REPO}/commits/{NEW_HEAD}/status?per_page=100"] = {
        "total_count": 0, "statuses": [], "state": "pending",
    }
    db[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": MAIN}}
    db[f"repos/{REPO}/compare/{MERGE}...{MAIN}"] = {
        "status": "ahead", "behind_by": 0, "ahead_by": 1,
        "merge_base_commit": {"sha": MERGE},
    }
    db[f"repos/{REPO}/issues/277"]["state_reason"] = "completed"
    return db


class SupersedeTests(unittest.TestCase):
    def setUp(self):
        self.db = db_fixture()
        self.issue = copy.deepcopy(ISSUE)
        for ctx in (
            patch.object(s, "api", side_effect=lambda route: copy.deepcopy(self.db[route])),
            patch.object(f, "api", side_effect=lambda route: copy.deepcopy(self.db[route])),
            patch.object(f.handoff, "read_issue",
                         side_effect=lambda key: copy.deepcopy(self.issue)),
            patch.object(f, "original_receipt", return_value=MANIFEST),
        ):
            ctx.start()
            self.addCleanup(ctx.stop)

    def ready(self):
        return s.review(KEY, PROJECT, REPLACEMENT, MERGE)

    def test_exact_closed_worker_and_distinct_merged_fix_yield_readonly_ready(self):
        with patch.object(f, "command") as command:
            data = self.ready()
            self.assertEqual(data["state"], "ready_to_cancel")
            self.assertEqual(data["original_pr"], 278)
            self.assertEqual(data["replacement_pr"], 281)
            self.assertEqual(data["replacement_merge_sha"], MERGE)
            self.assertEqual(data["verified_checks"], sorted(REQUIRED))
            command.assert_not_called()

    def test_original_pr_must_be_closed_unmerged_no_merge_sha(self):
        original = f"repos/{REPO}/pulls/{PR}"
        for upd in (
            {"state": "open"}, {"merged": True}, {"merge_commit_sha": MERGE},
            {"merged_at": "2026-10-24T13:00:14Z"}, {"closed_at": None},
            {"body": "Closes #276"}, {"number": 279},
            {"head": {"sha": HEAD, "repo": {"full_name": "wrong/repo"}}},
            {"base": {"ref": "release", "repo": {"full_name": REPO}}},
        ):
            with self.subTest(update=upd), self.assertRaises(ValueError):
                self.db = db_fixture()
                self.db[original].update(upd)
                self.ready()

    def test_original_receipt_scope_and_checkout_provenance_fail_closed(self):
        commits = f"repos/{REPO}/pulls/{PR}/commits?per_page=100"
        parent = f"repos/{REPO}/git/commits/{HEAD}"
        files = f"repos/{REPO}/pulls/{PR}/files?per_page=100"
        for path, item in (
            (commits, []),
            (commits, [{"sha": "0"*40}]),
            (parent, {"parents": [{"sha": "0"*40}]}),
            (files, []),
            (files, [{"filename": "fovux-mcp/uv.lock", "status": "modified"}]),
            (files, [{"filename": PATHS[0], "status": "removed"}]),
            (files, [{"filename": PATHS[0], "status": "modified"}]*100),
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.db = db_fixture()
                self.db[path] = item
                self.ready()

    def test_replacement_distinct_merged_valid_head_and_repo(self):
        path = f"repos/{REPO}/pulls/{REPLACEMENT}"
        for change in (
            {"merged": False}, {"state": "open"}, {"merged_at": None},
            {"number": 284}, {"merge_commit_sha": "0"*40},
            {"base": {"ref": "release", "repo": {"full_name": REPO}}},
            {"head": {"sha": NEW_HEAD, "repo": {"full_name": "foreign/repo"}}},
            {"head": {"sha": "invalid", "repo": {"full_name": REPO}}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.db = db_fixture()
                self.db[path].update(change)
                self.ready()
        with self.assertRaisesRegex(ValueError, "distinct"):
            s.review(KEY, PROJECT, PR, MERGE)

    def test_replacement_must_cover_every_original_changed_file(self):
        path = f"repos/{REPO}/pulls/{REPLACEMENT}/files?per_page=100"
        for files in (
            [{"filename": PATHS[0], "status": "modified"}],
            [{"filename": PATHS[0], "status": "modified"},
             {"filename": PATHS[1], "status": "removed"}],
            [],
            [{"filename": PATHS[0], "status": "modified"}]*100,
        ):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.db = db_fixture()
                self.db[path] = files
                self.ready()

    def test_branch_ancestry_cannot_be_diverged_or_missing(self):
        path = f"repos/{REPO}/compare/{MERGE}...{MAIN}"
        for upd in (
            {"status": "diverged"}, {"behind_by": 1},
            {"ahead_by": 0}, {"ahead_by": 251},
            {"merge_base_commit": {"sha": SOURCE}},
        ):
            with self.subTest(change=upd), self.assertRaises(ValueError):
                self.db = db_fixture()
                self.db[path].update(upd)
                self.ready()

    def test_target_issue_must_be_closed_and_completed(self):
        key = f"repos/{REPO}/issues/277"
        for upd in (
            {"state": "open"}, {"state_reason": "not_planned"},
            {"number": 278},
        ):
            with self.subTest(update=upd), self.assertRaises(ValueError):
                self.db = db_fixture()
                self.db[key].update(upd)
                self.ready()

    def test_required_or_optional_failure_and_missing_rule_block(self):
        checks = f"repos/{REPO}/commits/{NEW_HEAD}/check-runs?per_page=100"
        rules = f"repos/{REPO}/rules/branches/main?per_page=100"
        for conclusion in ("failure", "neutral", "skipped", "cancelled", "timed_out"):
            self.db = db_fixture()
            self.db[checks]["check_runs"][0]["conclusion"] = conclusion
            with self.subTest(result=conclusion), self.assertRaises(ValueError):
                self.ready()
        self.db = db_fixture()
        self.db[checks]["check_runs"][-1]["conclusion"] = "failure"
        with self.assertRaises(ValueError):
            self.ready()
        self.db = db_fixture()
        self.db[checks]["total_count"] = 101
        with self.assertRaises(ValueError):
            self.ready()
        self.db = db_fixture()
        self.db[rules] = []
        with self.assertRaises(ValueError):
            self.ready()

    def test_worker_execution_is_required_and_unmodified(self):
        run = f"repos/{WORKER}/actions/runs/{RUN}"
        for k,v in (("conclusion", "failure"), ("run_attempt", 2),
                    ("event", "push"), ("status", "in_progress")):
            self.db = db_fixture()
            self.db[run][k] = v
            with self.subTest(k=k), self.assertRaises(ValueError):
                self.ready()

    def test_multica_identity_not_mutated_for_unapproved_or_done_tasks(self):
        for change in (
            {"fleet_auto_merge": True}, {"fleet_ready": True},
            {"fleet_external_pr_only": False},
            {"fleet_model": "paid/model"},
            {"fleet_merge_sha": "c"*40},
            {"fleet_result": "completed"},
            {"fleet_superseded_by_pr": "281"},
        ):
            self.issue = {**ISSUE, "metadata": {**META, **change}}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ready()
        self.issue = {**ISSUE, "status": "done"}
        with self.assertRaisesRegex(ValueError, "Unexpected Multica"):
            self.ready()

    def test_cancelled_exact_proof_is_read_only_idempotent(self):
        self.issue = {**ISSUE, "status": "cancelled", "metadata": {
            **META, "fleet_result": "superseded",
            "fleet_superseded_by_pr": REPLACEMENT,
            "fleet_superseded_merge_sha": MERGE,
        }}
        self.assertEqual(self.ready()["state"], "already_superseded")
        for key, value in (("fleet_superseded_by_pr", "280"),
                           ("fleet_superseded_merge_sha", "0"*40),
                           ("fleet_result", "completed")):
            self.issue["metadata"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ready()
            self.issue["metadata"][key] = (REPLACEMENT if key.endswith("by_pr")
                                            else MERGE if key.endswith("merge_sha")
                                            else "superseded")

    def test_apply_only_exact_fully_reviewed_task_does_not_merge_or_dispatch(self):
        result = self.ready()
        with patch.object(f, "command", return_value="") as command:
            for args in ((KEY, REPLACEMENT, "0"*40),
                         ("OAS-31", REPLACEMENT, MERGE),
                         (KEY, PR, MERGE)):
                with self.assertRaisesRegex(ValueError, "not authorized"):
                    s.apply(result, *args)
            command.assert_not_called()
            with self.assertRaisesRegex(ValueError, "readback"):
                s.apply(result, KEY, REPLACEMENT, MERGE)
            self.assertEqual(command.call_count, 4)
            self.assertNotIn("gh", str(command.call_args_list))
            self.assertNotIn("done", str(command.call_args_list))
            self.assertIn("cancelled", str(command.call_args_list))
            self.assertNotIn("fleet_merge_sha", str(command.call_args_list))
            self.issue = {**ISSUE, "status": "cancelled", "metadata": {
                **META, "fleet_result": "superseded",
                "fleet_superseded_by_pr": REPLACEMENT,
                "fleet_superseded_merge_sha": MERGE,
            }}
            self.assertEqual(s.apply(result, KEY, REPLACEMENT, MERGE), "cancelled")
            self.assertEqual(command.call_count, 8)

    def test_apply_rejects_prior_completion_states(self):
        with patch.object(f, "command") as command:
            for state in ("waiting", "already_superseded", "blocked", "ready"):
                with self.assertRaises(ValueError):
                    s.apply({"state": state, "issue": KEY,
                             "replacement_pr": int(REPLACEMENT),
                             "replacement_merge_sha": MERGE},
                            KEY, REPLACEMENT, MERGE)
            command.assert_not_called()

    def test_cli_mutation_requires_confirmed_semantic_review(self):
        common=["tool", "--issue", KEY, "--project-id", PROJECT,
                "--replacement-pr", REPLACEMENT,
                "--replacement-merge-sha", MERGE, "--apply"]
        import sys
        with patch.object(s, "review", return_value=self.ready()), \
             patch.object(s, "apply") as mutation:
            for args in (
                common,
                common+["--confirm-issue",KEY,
                        "--confirm-replacement-pr",REPLACEMENT,
                        "--confirm-merge-sha",MERGE],
                common+["--confirm-issue",KEY,
                        "--confirm-replacement-pr","280",
                        "--confirm-merge-sha",MERGE,
                        "--confirm-resolution-reviewed"],
            ):
                with self.subTest(args=args),patch.object(sys,"argv",args):
                    self.assertEqual(s.main(),2)
            mutation.assert_not_called()


if __name__=="__main__":
    unittest.main()
