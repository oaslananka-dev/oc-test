#!/usr/bin/env python3
"""F2.4 offline tests: artifact provenance, patch shape, conflicts, local-only branch."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ocfilo_f2_dag as dag
import ocfilo_f2_integrate as integ
import ocfilo_f2_pilot as pilot

ROOT = Path(__file__).resolve().parents[2]
RAW = json.loads((ROOT / "examples/ocfilo-f2-dag-pilot.json").read_text(encoding="utf-8-sig"))
RUN, ATTEMPT = 4242, 1
ROUNDS = {"f2-dag-audit": 1, "f2-dag-backend": 2, "f2-dag-frontend": 2, "f2-dag-report": 3}
PATHS = {t: x["allowed_paths"][0] for t, x in dag.read_dag(RAW).items()}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True,
                          text=True).stdout.strip()


def make_repo(root):
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@invalid.test")
    git(root, "config", "user.name", "Offline Test")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


def real_patch(base_repo, path, text):
    """Produce the exact patch shape the F2 worker emits (git add -N + diff)."""
    target = Path(base_repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(base_repo, "add", "-N", "--", path)
    out = subprocess.run(["git", "-C", str(base_repo), "diff", "--binary", "--no-ext-diff",
                          "HEAD", "--", path], capture_output=True, check=True).stdout
    git(base_repo, "reset", "-q", "--", path)
    target.unlink()
    return out


def evidence(tid, sha, outcome="SUCCEEDED", cls=None, run=RUN, attempt=ATTEMPT):
    return {
        "schema_version": 2, "repository": pilot.REPO, "repository_id": int(pilot.REPO_ID),
        "issue_number": 10, "model": pilot.MODEL, "identity": "actions-readonly",
        "allowed_paths": [PATHS[tid]], "task_id": tid, "round": ROUNDS[tid],
        "checkout_sha": sha, "idempotency_key": pilot.idempotency_key(sha, tid),
        "workflow_run_id": run, "run_attempt": attempt, "worker_outcome": outcome,
        "failure_class": cls, "retryable": cls in pilot.RETRYABLE,
        "agent_exit_code": 0 if outcome == "SUCCEEDED" else 1, "agent_seconds": 12,
        "deadline_seconds": 300, "scope_verified": outcome == "SUCCEEDED", "published": False,
    }


def run_meta(sha, **over):
    meta = {"id": RUN, "run_attempt": ATTEMPT, "head_sha": sha, "head_branch": "main",
            "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
            "path": integ.WORKFLOW_PATH,
            "repository": {"full_name": pilot.REPO, "id": int(pilot.REPO_ID)}}
    meta.update(over)
    return meta


class Fixture:
    """Temp base repo + artifacts directory for a complete run."""

    def __init__(self, test):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.sha = make_repo(self.repo)
        scratch = self.root / "scratch"
        git(self.root, "clone", "-q", str(self.repo), str(scratch))
        self.patches = {t: real_patch(scratch, p, f"{t} output\n") for t, p in PATHS.items()}
        self.art = self.root / "art"
        self.art.mkdir()
        for tid in PATHS:
            self.write(tid)

    def dirname(self, tid, run=RUN, attempt=ATTEMPT):
        return f"ocfilo-f2-{run}-{attempt}-r{ROUNDS[tid]}-{tid}"

    def write(self, tid, ev=None, patch="default"):
        d = self.art / self.dirname(tid)
        d.mkdir(exist_ok=True)
        ev = ev or evidence(tid, self.sha)
        (d / "result.json").write_text(json.dumps(ev), encoding="utf-8")
        data = self.patches[tid] if patch == "default" else patch
        target = d / "changes.patch"
        if data is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(data)
        return d

    def integrate(self, **kw):
        kw.setdefault("meta", run_meta(self.sha))
        kw.setdefault("sha", self.sha)
        meta = kw["meta"]
        run_id = meta.get("id", RUN) if isinstance(meta, dict) else RUN
        return integ.integrate(RAW, self.art, meta, run_id, kw["sha"], self.repo, kw.get("branch"))


class HappyPathTests(unittest.TestCase):
    def test_dry_run_verifies_without_touching_the_repo(self):
        fx = Fixture(self)
        m = fx.integrate()
        self.assertEqual([t["task_id"] for t in m["tasks"]],
                         ["f2-dag-audit", "f2-dag-backend", "f2-dag-frontend", "f2-dag-report"])
        self.assertIsNone(m["branch"])
        self.assertIsNone(m["commit"])
        self.assertEqual((m["pushed"], m["review_required"], m["merge_allowed"]), (False, True, False))
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")

    def test_branch_contains_exactly_the_owned_files(self):
        fx = Fixture(self)
        m = fx.integrate(branch="f2-integration/run-4242")
        self.assertEqual(m["commit"], git(fx.repo, "rev-parse", "HEAD"))
        changed = git(fx.repo, "diff", "--name-only", fx.sha, "HEAD").split("\n")
        self.assertEqual(sorted(changed), sorted(PATHS.values()))
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")
        self.assertEqual(git(fx.repo, "log", "-1", "--format=%an <%ae>"),
                         "Offline Test <t@invalid.test>")
        self.assertEqual((fx.repo / PATHS["f2-dag-report"]).read_text(), "f2-dag-report output\n")
        self.assertEqual(git(fx.repo, "remote"), "")  # nothing to push to

    def test_same_input_gives_same_tree(self):
        a, b = Fixture(self), Fixture(self)
        a.integrate(branch="f2-integration/x")
        b.integrate(branch="f2-integration/x")
        self.assertEqual(git(a.repo, "rev-parse", "HEAD^{tree}"), git(b.repo, "rev-parse", "HEAD^{tree}"))

    def test_manifest_hashes_match_patches(self):
        import hashlib
        fx = Fixture(self)
        m = fx.integrate()
        for t in m["tasks"]:
            self.assertEqual(t["patch_sha256"], hashlib.sha256(fx.patches[t["task_id"]]).hexdigest())


class CompletenessTests(unittest.TestCase):
    def refuse(self, fx, **kw):
        with self.assertRaises(ValueError):
            fx.integrate(**kw)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")

    def test_failed_task_refused(self):
        fx = Fixture(self)
        fx.write("f2-dag-backend", evidence("f2-dag-backend", fx.sha, "FAILED", "TIMEOUT"), None)
        self.refuse(fx, branch="f2-integration/x")

    def test_missing_task_refused(self):
        fx = Fixture(self)
        for f in (fx.art / fx.dirname("f2-dag-report")).iterdir():
            f.unlink()
        (fx.art / fx.dirname("f2-dag-report")).rmdir()
        self.refuse(fx)

    def test_two_successes_for_one_task_refused(self):
        fx = Fixture(self)
        ev = evidence("f2-dag-audit", fx.sha)
        ev["round"] = 2
        d = fx.art / f"ocfilo-f2-{RUN}-{ATTEMPT}-r2-f2-dag-audit"
        d.mkdir()
        (d / "result.json").write_text(json.dumps(ev), encoding="utf-8")
        (d / "changes.patch").write_bytes(fx.patches["f2-dag-audit"])
        self.refuse(fx)

    def test_patch_presence_must_match_outcome(self):
        fx = Fixture(self)
        fx.write("f2-dag-audit", patch=None)
        self.refuse(fx)
        fx = Fixture(self)
        fx.write("f2-dag-backend", evidence("f2-dag-backend", fx.sha, "FAILED", "TIMEOUT"))
        self.refuse(fx)


class ProvenanceTests(unittest.TestCase):
    def refuse(self, fx, **kw):
        with self.assertRaises(ValueError):
            fx.integrate(**kw)
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)

    def test_unexpected_artifact_or_file(self):
        fx = Fixture(self)
        (fx.art / "evil-artifact").mkdir()
        self.refuse(fx)
        fx = Fixture(self)
        (fx.art / fx.dirname("f2-dag-audit") / "extra.sh").write_text("x")
        self.refuse(fx)

    def test_dagstate_artifact_is_ignored(self):
        fx = Fixture(self)
        (fx.art / f"ocfilo-f2-dagstate-{RUN}-{ATTEMPT}").mkdir()
        self.assertEqual(len(fx.integrate()["tasks"]), 4)

    def test_artifact_name_and_evidence_must_agree(self):
        for field, value in (("workflow_run_id", 1), ("run_attempt", 2), ("round", 5),
                             ("task_id", "f2-dag-report")):
            fx = Fixture(self)
            ev = evidence("f2-dag-audit", fx.sha)
            ev[field] = value
            fx.write("f2-dag-audit", ev)
            self.refuse(fx)

    def test_wrong_checkout_sha_in_evidence(self):
        fx = Fixture(self)
        fx.write("f2-dag-audit", evidence("f2-dag-audit", "f" * 40))
        self.refuse(fx)

    def test_duplicate_json_evidence_keys_are_refused_before_integration(self):
        fx = Fixture(self)
        d = fx.art / fx.dirname("f2-dag-audit")
        original = (d / "result.json").read_text()
        for mutation in (
            original.replace('"task_id": "f2-dag-audit"',
                             '"task_id": "f2-dag-audit", "task_id": "f2-dag-audit"'),
            original.replace('"published": false',
                             '"published": false, "extra_root_key": 1'),
            original.replace('"published": false', '"published": NaN'),
        ):
            with self.subTest(mutation=mutation[-65:]):
                (d / "result.json").write_text(mutation)
                self.refuse(fx, branch="f2-integration/invalid-json")
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")

    def test_tampered_evidence(self):
        for key, value in (("published", True), ("scope_verified", False),
                           ("idempotency_key", "0" * 64), ("model", "paid/model"),
                           ("allowed_paths", ["playground/f2/dag/other.txt"])):
            fx = Fixture(self)
            ev = evidence("f2-dag-audit", fx.sha)
            ev[key] = value
            fx.write("f2-dag-audit", ev)
            self.refuse(fx)

    def test_run_metadata_must_be_trusted_manual_main_run(self):
        fx = Fixture(self)
        bad = {"id": RUN + 1, "head_sha": "f" * 40, "head_branch": "dev", "event": "push",
               "path": ".github/workflows/other.yml", "status": "in_progress",
               "conclusion": "failure", "run_attempt": "1",
               "repository": {"full_name": "evil/repo", "id": int(pilot.REPO_ID)}}
        for key, value in bad.items():
            with self.subTest(key=key):
                meta = run_meta(fx.sha, **{key: value})
                self.refuse(fx, meta=meta)
        self.refuse(fx, meta=run_meta(fx.sha, repository={"full_name": pilot.REPO, "id": 1}))
        self.refuse(fx, meta=run_meta(fx.sha, conclusion=None))
        self.refuse(fx, meta="not-a-dict")

    def test_run_id_must_match_artifacts(self):
        fx = Fixture(self)
        with self.assertRaises(ValueError):
            integ.integrate(RAW, fx.art, run_meta(fx.sha, id=RUN + 1), RUN + 1, fx.sha, fx.repo)


class PatchShapeTests(unittest.TestCase):
    OWN = "playground/f2/dag/audit.txt"
    GOOD = (f"diff --git a/{OWN} b/{OWN}\nnew file mode 100644\nindex 0000000..e69de29\n"
            f"--- /dev/null\n+++ b/{OWN}\n@@ -0,0 +1 @@\n+hello\n").encode()

    def bad(self, patch):
        with self.assertRaises(ValueError):
            integ.check_patch(patch, self.OWN)

    def test_real_shape_accepted(self):
        integ.check_patch(self.GOOD, self.OWN)

    def test_attacks_rejected(self):
        g = self.GOOD.decode()
        other = "playground/f2/dag/backend.txt"
        cases = {
            "other file": g.replace(self.OWN, other),
            "second file": g + f"diff --git a/{other} b/{other}\nnew file mode 100644\n"
                               f"index 0000000..e69de29\n--- /dev/null\n+++ b/{other}\n"
                               "@@ -0,0 +1 @@\n+x\n",
            "executable": g.replace("100644", "100755"),
            "symlink": g.replace("100644", "120000"),
            "rename": g.replace("new file mode 100644\n", "rename from x\nrename to y\n"),
            "deleted": g.replace("new file mode 100644", "deleted file mode 100644"),
            "binary": g.replace("@@ -0,0 +1 @@\n+hello\n", "GIT binary patch\nliteral 0\n"),
            "no hunk": g.split("@@")[0],
            "stray line": g + "echo pwned\n",
            "blank line": g + "\n",
            "bad marker": g.replace("--- /dev/null", "--- a/x"),
            "mismatched b": g.replace("+++ b/" + self.OWN, "+++ b/elsewhere.txt"),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.bad(text.encode())

    def test_size_encoding_and_truncation(self):
        self.bad(b"")
        self.bad(self.GOOD + b"+" + b"x" * pilot.MAX_BYTES + b"\n")
        self.bad(self.GOOD + b"+\xff\xfe\n")
        self.bad(self.GOOD.rstrip(b"\n"))
        self.bad(b"diff --git a/x b/x\n")
        self.bad(b"\n")


class RepositoryStateTests(unittest.TestCase):
    def refuse(self, fx, **kw):
        with self.assertRaises(ValueError):
            fx.integrate(**kw)

    def test_head_must_be_the_evidence_sha(self):
        fx = Fixture(self)
        (fx.repo / "other.txt").write_text("x")
        git(fx.repo, "add", "other.txt")
        git(fx.repo, "commit", "-qm", "later")
        self.refuse(fx, branch="f2-integration/x")

    def test_dirty_worktree_refused(self):
        fx = Fixture(self)
        (fx.repo / "stray.txt").write_text("x")
        self.refuse(fx, branch="f2-integration/x")

    def test_conflicting_existing_file_refused_and_no_branch_left(self):
        fx = Fixture(self)
        # the base already has audit.txt with different content: patch is a new-file patch
        target = fx.repo / PATHS["f2-dag-audit"]
        target.parent.mkdir(parents=True)
        target.write_text("different\n")
        git(fx.repo, "add", "-A")
        git(fx.repo, "commit", "-qm", "occupied")
        new_sha = git(fx.repo, "rev-parse", "HEAD")
        for tid in PATHS:
            fx.write(tid, evidence(tid, new_sha))
        with self.assertRaises(ValueError):
            integ.integrate(RAW, fx.art, run_meta(new_sha), RUN, new_sha, fx.repo,
                            "f2-integration/x")
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), new_sha)

    def test_final_owned_paths_invariant_rolls_back(self):
        from unittest.mock import patch
        fx = Fixture(self)
        scratch = fx.root / "scratch2"
        git(fx.root, "clone", "-q", str(fx.repo), str(scratch))
        two = (real_patch(scratch, PATHS["f2-dag-audit"], "a\n")
               + real_patch(scratch, "playground/f2/dag/smuggled.txt", "s\n"))
        fx.write("f2-dag-audit", patch=two)
        with patch.object(integ, "check_patch", lambda *a: None):  # bypass the first gate
            with self.assertRaisesRegex(ValueError, "differs from the owned paths"):
                fx.integrate(branch="f2-integration/x")
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")
        self.assertEqual(git(fx.repo, "status", "--porcelain", "--untracked-files=all"), "")

    def test_branch_name_rules_and_reuse(self):
        fx = Fixture(self)
        for name in ("main", "../x", "f2-integration/Bad Name", "f2-integration/", "x/y"):
            self.refuse(fx, branch=name)
        fx.integrate(branch="f2-integration/once")
        git(fx.repo, "checkout", "-q", "--detach", fx.sha)
        self.refuse(fx, branch="f2-integration/once")


class CliTests(unittest.TestCase):
    def test_cli_dry_run_writes_manifest_and_never_overwrites(self):
        import os
        fx = Fixture(self)
        meta = fx.root / "run.json"
        meta.write_text(json.dumps(run_meta(fx.sha)), encoding="utf-8")
        out = fx.root / "manifest.json"
        old = os.getcwd()
        os.chdir(ROOT)
        try:
            args = ["--plan", "dag-pilot", "--artifacts", str(fx.art), "--run-json", str(meta),
                    "--sha", fx.sha, "--repo", str(fx.repo), "--manifest", str(out)]
            integ.main(args)
            data = json.loads(out.read_text())
            self.assertEqual((data["pushed"], data["merge_allowed"]), (False, False))
            with self.assertRaises(SystemExit):
                integ.main(args)
            with self.assertRaises(SystemExit):
                integ.main(args[:-2] + ["--manifest", str(fx.root / "m2.json"), "--branch", "main"])
        finally:
            os.chdir(old)

    def test_cli_existing_manifest_does_not_create_branch(self):
        fx = Fixture(self)
        meta = fx.root / "run.json"
        meta.write_text(json.dumps(run_meta(fx.sha)), encoding="utf-8")
        out = fx.root / "manifest.json"
        out.write_text("keep-existing-manifest", encoding="utf-8")
        args = ["--plan", "dag-pilot", "--artifacts", str(fx.art), "--run-json", str(meta),
                "--sha", fx.sha, "--repo", str(fx.repo), "--manifest", str(out),
                "--branch", "f2-integration/collision"]
        with self.assertRaises(SystemExit):
            integ.main(args)
        self.assertEqual(out.read_text(encoding="utf-8"), "keep-existing-manifest")
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")
    def test_cli_missing_manifest_parent_does_not_create_branch(self):
        fx = Fixture(self)
        meta = fx.root / "run.json"
        meta.write_text(json.dumps(run_meta(fx.sha)), encoding="utf-8")
        out = fx.root / "not-created" / "manifest.json"
        args = ["--plan", "dag-pilot", "--artifacts", str(fx.art), "--run-json", str(meta),
                "--sha", fx.sha, "--repo", str(fx.repo), "--manifest", str(out),
                "--branch", "f2-integration/missing-parent"]
        with self.assertRaises(SystemExit):
            integ.main(args)
        self.assertFalse(out.exists())
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")

    def test_cli_partial_manifest_write_failure_rolls_back_owned_branch(self):
        fx = Fixture(self)
        meta = fx.root / "run.json"
        meta.write_text(json.dumps(run_meta(fx.sha)), encoding="utf-8")
        out = fx.root / "manifest.json"
        args = ["--plan", "dag-pilot", "--artifacts", str(fx.art), "--run-json", str(meta),
                "--sha", fx.sha, "--repo", str(fx.repo), "--manifest", str(out),
                "--branch", "f2-integration/partial-write"]
        def failing_write(payload, stream, **_kwargs):
            stream.write("partial-manifest")
            raise OSError("simulated output write error")
        with patch.object(integ.json, "dump", side_effect=failing_write):
            with self.assertRaises(SystemExit):
                integ.main(args)
        self.assertFalse(out.exists(), "Partial manifest must be removed")
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")

    def test_cli_existing_symlink_manifest_is_not_followed(self):
        fx = Fixture(self)
        meta = fx.root / "run.json"
        meta.write_text(json.dumps(run_meta(fx.sha)), encoding="utf-8")
        existing = fx.root / "external.json"
        existing.write_text("do-not-overwrite", encoding="utf-8")
        link = fx.root / "manifest-link.json"
        try:
            link.symlink_to(existing)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable on this OS: {exc}")
        args = ["--plan", "dag-pilot", "--artifacts", str(fx.art), "--run-json", str(meta),
                "--sha", fx.sha, "--repo", str(fx.repo), "--manifest", str(link),
                "--branch", "f2-integration/symlink"]
        with self.assertRaises(SystemExit):
            integ.main(args)
        self.assertEqual(existing.read_text(encoding="utf-8"), "do-not-overwrite")
        self.assertTrue(link.is_symlink())
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD"), fx.sha)
        self.assertEqual(git(fx.repo, "branch", "--list", "f2-integration/*"), "")

    def test_module_never_pushes(self):
        src = (ROOT / ".github/scripts/ocfilo_f2_integrate.py").read_text(encoding="utf-8")
        for word in ('"push"', '"fetch"', '"remote"', "gh ", "requests", "urllib"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
