"""Regression: only a verified real GitHub user can author published commits."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from oc_fleet_identity import (
    github_user_identity, validate_local_identity, verify_commit
)


class GitIdentityTests(unittest.TestCase):
    USER = {"login": "oaslananka", "id": 285490571,
            "name": "Osman Aslan", "type": "User"}

    def test_real_account_uses_recognized_noreply_address(self):
        self.assertEqual(github_user_identity(self.USER),
                         ("Osman Aslan", "285490571+oaslananka@users.noreply.github.com"))

    def test_another_selected_user(self):
        identity = github_user_identity(
            {"login": "ExampleUser", "id": 42, "name": "", "type": "User"})
        self.assertEqual(identity, ("ExampleUser", "42+ExampleUser@users.noreply.github.com"))

    def test_fake_authors_are_rejected(self):
        for forbidden in [
            {"login":"github-actions[bot]","id":3,"type":"Bot"},
            {"login":"real-user","id":4,"type":"Bot"},
            {"login":"ocfilo-integrator","id":11,"type":"User"},
            {"login":"real-user","id":4,"name":"ocfilo-fleet-controller","type":"User"},
            {"login":"real-user","id":0,"type":"User"},
            {"login":"real-user","id":"abc","type":"User"},
        ]:
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                github_user_identity(forbidden)

    def test_local_identity_rejects_fabricated_bot(self):
        for name, email in (
            ("ocfilo-fleet-controller", "fleet-controller@users.noreply.github.com"),
            ("ocfilo-integrator", "ocfilo-integrator@users.noreply.github.com"),
            ("valid name", "ocfilo-integrator@users.noreply.github.com"),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_local_identity(name, email)

    def test_commit_provenance_exactly_matches_publisher(self):
        name,email=github_user_identity(self.USER)
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            def git(*args):
                return subprocess.run(["git", "-C", tmp, *args], check=True,
                                      capture_output=True, text=True)
            git("init","-q")
            git("config","user.name",name)
            git("config","user.email",email)
            (repo/"example.txt").write_text("fixed\n")
            git("add","example.txt")
            git("commit","-qm","test: cover edge cases")
            self.assertTrue(verify_commit(repo,name,email))
            with self.assertRaisesRegex(ValueError, "author/committer"):
                verify_commit(repo,"Other User",email)
            git("commit","--amend","--no-gpg-sign","-qm",
                "test: cover edge cases\n\nCo-authored-by: ocfilo-fleet-controller <fleet-controller@users.noreply.github.com>")
            with self.assertRaisesRegex(ValueError,"trailer"):
                verify_commit(repo,name,email)

if __name__=="__main__":
    unittest.main()
