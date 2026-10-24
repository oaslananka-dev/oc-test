"""Offline negative-first proofs for original-receipt-authorized base advancement."""
import copy
import unittest
from unittest.mock import patch

import oc_fleet_external_base_proof as p
import oc_fleet_external_finalize as finalize

REPO = "oaslananka/adxl355"
SOURCE, PARENT = "a"*40, "b"*40
OLD_ROOT, NEW_ROOT = "c"*40, "d"*40
OLD_DIR, NEW_DIR = "e"*40, "f"*40
OLD_FLEET, NEW_FLEET = "1"*40, "2"*40
FILE = "playground/fleet/base-recovery.json"
BLOB = "3"*40
CHANGED = "4"*40


def fixture():
    return {
        f"repos/{REPO}/compare/{SOURCE}...{PARENT}": {
            "status": "ahead", "behind_by": 0, "ahead_by": 2,
            "total_commits": 2, "base_commit": {"sha": SOURCE},
            "merge_base_commit": {"sha": SOURCE},
        },
        f"repos/{REPO}/git/commits/{SOURCE}": {
            "sha": SOURCE, "tree": {"sha": OLD_ROOT},
        },
        f"repos/{REPO}/git/commits/{PARENT}": {
            "sha": PARENT, "tree": {"sha": NEW_ROOT},
        },
        f"repos/{REPO}/git/trees/{OLD_ROOT}": {
            "sha": OLD_ROOT, "truncated": False,
            "tree": [{"path": "playground", "type": "tree",
                      "mode": "040000", "sha": OLD_DIR}],
        },
        f"repos/{REPO}/git/trees/{NEW_ROOT}": {
            "sha": NEW_ROOT, "truncated": False,
            "tree": [{"path": "playground", "type": "tree",
                      "mode": "040000", "sha": NEW_DIR}],
        },
        f"repos/{REPO}/git/trees/{OLD_DIR}": {
            "sha": OLD_DIR, "truncated": False,
            "tree": [{"path": "fleet", "type": "tree",
                      "mode": "040000", "sha": OLD_FLEET}],
        },
        f"repos/{REPO}/git/trees/{NEW_DIR}": {
            "sha": NEW_DIR, "truncated": False,
            "tree": [{"path": "fleet", "type": "tree",
                      "mode": "040000", "sha": NEW_FLEET}],
        },
        f"repos/{REPO}/git/trees/{OLD_FLEET}": {
            "sha": OLD_FLEET, "truncated": False,
            "tree": [{"path": "base-recovery.json", "type": "blob",
                      "mode": "100644", "sha": BLOB}],
        },
        f"repos/{REPO}/git/trees/{NEW_FLEET}": {
            "sha": NEW_FLEET, "truncated": False,
            "tree": [{"path": "base-recovery.json", "type": "blob",
                      "mode": "100644", "sha": BLOB}],
        },
    }


class BaseProofTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def api(self, route):
        return copy.deepcopy(self.data[route])

    def check(self, paths=None):
        return p.validate(REPO, SOURCE, PARENT, paths or [FILE], self.api)

    def test_unmodified_blob_under_advanced_parent_accepted(self):
        self.assertTrue(self.check())

    def test_identical_absent_original_and_new_file_proven_safe(self):
        self.assertTrue(self.check(["playground/fleet/new.json"]))

    def test_original_file_modified_rejected(self):
        self.data[f"repos/{REPO}/git/trees/{NEW_FLEET}"]["tree"][0]["sha"] = CHANGED
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check()

    def test_original_missing_and_added_during_advance_rejected(self):
        self.data[f"repos/{REPO}/git/trees/{OLD_FLEET}"]["tree"].clear()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check()

    def test_original_present_and_removed_during_advance_rejected(self):
        self.data[f"repos/{REPO}/git/trees/{NEW_FLEET}"]["tree"].clear()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check()

    def test_directory_scope_uses_exact_subtree_sha_not_names(self):
        self.data[f"repos/{REPO}/git/trees/{NEW_ROOT}"]["tree"][0]["sha"] = OLD_DIR
        self.assertTrue(self.check(["playground/"]))
        self.data = fixture()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.check(["playground/"])

    def test_ancestry_must_be_exact_bounded_descendant(self):
        url=f"repos/{REPO}/compare/{SOURCE}...{PARENT}"
        for fields in (
            {"status": "diverged"}, {"behind_by": 1},
            {"merge_base_commit": {"sha": CHANGED}},
            {"base_commit": {"sha": CHANGED}},
            {"ahead_by": 251, "total_commits": 251},
            {"ahead_by": 1, "total_commits": 2},
            {"status": "behind"},
        ):
            self.data = fixture()
            self.data[url].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.check()

    def test_tree_inventory_untrusted_or_incomplete_refused(self):
        url=f"repos/{REPO}/git/trees/{NEW_DIR}"
        for fields in (
            {"truncated": True}, {"sha": CHANGED},
            {"tree": None},
            {"tree": [fixture()[url]["tree"][0]] * 2},
        ):
            self.data = fixture()
            self.data[url].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.check()

    def test_tree_type_and_mode_changes_rejected(self):
        url=f"repos/{REPO}/git/trees/{NEW_FLEET}"
        for fields in ({"type": "tree"}, {"mode": "100755"},
                       {"type": "commit"}, {"sha": "unsafe"}):
            self.data = fixture()
            self.data[url]["tree"][0].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.check()

    def test_bad_scopes_and_invalid_sha_refused(self):
        for path in ("../a", "/a", "dir//bad", "p/./q", "p/../q", "p\\q"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.check([path])
        with self.assertRaises(ValueError):
            p.validate(REPO, SOURCE, SOURCE, [FILE], self.api)
        with self.assertRaises(ValueError):
            p.validate(REPO, SOURCE, PARENT, [FILE]*13, self.api)

    def test_opaque_github_error_fails_closed(self):
        self.data.pop(f"repos/{REPO}/git/trees/{NEW_DIR}")
        with self.assertRaises(KeyError):
            self.check()

    def test_reconciler_refuses_advance_without_original_opt_in(self):
        metadata = {"fleet_target_repo": REPO, "fleet_pull_request": "145",
                    "fleet_target_issue": "140"}
        changed = {
            f"repos/{REPO}/pulls/145": {
                "number": 145, "merged": True, "state": "closed",
                "merged_at": "2026-10-24T13:00:00Z",
                "merge_commit_sha": "5"*40, "body": "Closes #140",
                "head": {"sha": "6"*40, "repo": {"full_name": REPO}},
                "base": {"ref": "main", "repo": {"full_name": REPO}},
            },
            f"repos/{REPO}/pulls/145/commits?per_page=100": [
                {"sha": "6"*40}],
            f"repos/{REPO}/git/commits/{'6'*40}": {
                "parents": [{"sha": PARENT}]},
            f"repos/{REPO}/pulls/145/files?per_page=100": [
                {"filename": FILE, "status": "added"}],
        }
        with patch.object(finalize, "api", side_effect=lambda route: changed[route]):
            with self.assertRaisesRegex(ValueError, "without explicit"):
                finalize.trusted_pr(metadata,[FILE],SOURCE,allow_advance=False)
            with patch.object(finalize.base_proof,"validate",return_value=True) as verify:
                pr,head=finalize.trusted_pr(metadata,[FILE],SOURCE,allow_advance=True)
                self.assertEqual(pr["number"],145)
                self.assertEqual(head,"6"*40)
                verify.assert_called_once()

    def test_advance_proof_must_not_be_boolean_metadata_only(self):
        metadata={"fleet_target_repo":REPO,"fleet_pull_request":"145",
                  "fleet_target_issue":"140"}
        head="6"*40
        doc={
            f"repos/{REPO}/pulls/145": {
                "number":145, "merged":True, "state":"closed",
                "merged_at":"2026-10-24T13:00:00Z",
                "merge_commit_sha":"5"*40, "body":"Closes #140",
                "head":{"sha":head,"repo":{"full_name":REPO}},
                "base":{"ref":"main","repo":{"full_name":REPO}}},
            f"repos/{REPO}/pulls/145/commits?per_page=100":[{"sha":head}],
            f"repos/{REPO}/git/commits/{head}":{"parents":[{"sha":PARENT}]},
        }
        with patch.object(finalize,"api",side_effect=lambda route: doc[route]):
            with patch.object(finalize.base_proof,"validate",side_effect=ValueError("changed")):
                with self.assertRaisesRegex(ValueError,"changed"):
                    finalize.trusted_pr(metadata,[FILE],SOURCE,allow_advance=True)


if __name__ == "__main__":
    unittest.main()
