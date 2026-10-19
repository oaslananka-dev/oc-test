#!/usr/bin/env python3
"""Offline regression tests for the portable task contract."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
spec=importlib.util.spec_from_file_location("fleet",Path(__file__).with_name("oc_fleet_contract.py"))
fleet=importlib.util.module_from_spec(spec);spec.loader.exec_module(fleet)
BASE={"schema_version":1,"task_id":"pilot-001","source":"frontier","repository":"oaslananka-dev/oc-test",
"checkout_sha":"a"*40,"base_branch":"main","issue_key":"OCFI-42","prompt":"Fix the stated issue and add tests.",
"model":"opencode/big-pickle","profile":"base","allowed_paths":["playground/fleet/"],"phase":"implement","publish":False}
def parse(change=None,allowed=None):
    return fleet.parse_manifest(json.dumps({**BASE,**(change or {})}),allowed or [BASE["repository"]])
class FleetTests(unittest.TestCase):
    def test_sources(self):
        for s in ("frontier","rules"): self.assertEqual(parse({"source":s})["source"],s)
    def test_profiles(self):
        for p in fleet.PROFILES: self.assertEqual(parse({"profile":p})["profile"],p)
    def test_reject_invalid(self):
        for v in [{"source":"other"},{"task_id":"../secret"},{"repository":"evil/repo"},
                  {"checkout_sha":"main"},{"base_branch":"../main"},{"issue_key":"bad"},
                  {"prompt":"no"},{"model":"private/paid"},{"profile":"url"},
                  {"publish":True},{"phase":"repair"},{"schema_version":True},
                  {"allowed_paths":[".github/"]},{"allowed_paths":["playground/../"]},
                  {"allowed_paths":["playground/.env"]},{"allowed_paths":[1]},
                  {"allowed_paths":["playground/fleet/","playground/FLEET/"]},
                  {"arbitrary_plugin_url":"https://evil.invalid"}]:
            with self.subTest(v=v),self.assertRaises((ValueError,TypeError)): parse(v)
    def test_named_doppler_tool_profiles(self):
        self.assertEqual(parse().get("tool_profile"),None)
        for value in ("python-docs","context7-mcp","frontend3"):
            self.assertEqual(parse({"tool_profile":value})["tool_profile"],value)
        for value in ("../path","Python-Docs","with space","",7,"x"*80):
            if value == "":
                self.assertEqual(parse({"tool_profile":value})["tool_profile"],"")
                continue
            with self.subTest(value=value),self.assertRaises((TypeError,ValueError)):
                parse({"tool_profile":value})

    def test_optional_operator_selected_multica_issue(self):
        self.assertNotIn("multica_issue",parse())
        self.assertEqual(parse({"multica_issue":"OAS-11"})["multica_issue"],"OAS-11")
        self.assertEqual(parse({"multica_issue":""})["multica_issue"],"")
        for invalid in ("oas-11","OAS-0","OAS-01","../OAS-11","OAS-11\\n@all",7):
            with self.subTest(invalid=invalid),self.assertRaises((ValueError,TypeError)):
                parse({"multica_issue":invalid})

    def test_scope(self):
        self.assertTrue(fleet.path_allowed("playground/fleet/a.py",BASE["allowed_paths"]))
        for path in ("playground/fleets/a.py","playground/fleet/.env","docs/x.py","playground/fleet/../x"):
            self.assertFalse(fleet.path_allowed(path,BASE["allowed_paths"]))
    def test_allowlist(self):
        self.assertEqual(parse(allowed=["OASLANANKA-DEV/OC-TEST"])["task_id"],"pilot-001")
        with self.assertRaises(ValueError): parse(allowed=["different/repo"])
    def test_patch_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*a): return subprocess.check_output(["git","-C",str(root),*a],text=True).strip()
            git("init","-q");git("config","user.name","Fleet");git("config","user.email","fleet@example.invalid")
            (root/"playground/fleet").mkdir(parents=True)
            p=root/"playground/fleet/a.txt";p.write_text("old\n")
            git("add",".");git("commit","-qm","base");p.write_text("new\n")
            paths,patch=fleet.scoped_patch(root,BASE["allowed_paths"])
            self.assertEqual(paths,["playground/fleet/a.txt"]);self.assertIn(b"+new",patch)
            ev=fleet.create_evidence({**BASE,"checkout_sha":git("rev-parse","HEAD")},root,root/"results",1,1)
            self.assertEqual(ev["outcome"],"SUCCEEDED");self.assertFalse(ev["published"])
            (root/"unrelated.txt").write_text("bad")
            with self.assertRaises(ValueError): fleet.scoped_patch(root,BASE["allowed_paths"])
    def test_local_agent_commit_is_normalized_and_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Fleet")
            git("config", "user.email", "fleet@example.invalid")
            (root / "playground/fleet").mkdir(parents=True)
            target = root / "playground/fleet/receipt.json"
            target.write_text('{"status":"before"}\n')
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            target.write_text('{"status":"after"}\n')
            git("add", ".")
            git("commit", "-qm", "agent committed local changes")
            self.assertNotEqual(git("rev-parse", "HEAD"), base)
            evidence = fleet.create_evidence(
                {**BASE, "checkout_sha": base}, root, root / "result", 100, 1
            )
            self.assertEqual(git("rev-parse", "HEAD"), base)
            self.assertEqual(evidence["paths"], ["playground/fleet/receipt.json"])
            self.assertIn(b'+{"status":"after"}', (root / "result/changes.patch").read_bytes())

    def test_committed_out_of_scope_change_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Fleet")
            git("config", "user.email", "fleet@example.invalid")
            (root / "playground/fleet").mkdir(parents=True)
            (root / "playground/fleet/keep.txt").write_text("base\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "out-of-scope.txt").write_text("not allowed\n")
            git("add", ".")
            git("commit", "-qm", "unauthorized file")
            with self.assertRaisesRegex(ValueError, "outside approved scope"):
                fleet.create_evidence({**BASE, "checkout_sha": base},
                                      root, root / "result", 101, 1)

    def test_unrelated_head_is_never_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Fleet")
            git("config", "user.email", "fleet@example.invalid")
            (root / "playground/fleet").mkdir(parents=True)
            (root / "playground/fleet/keep.txt").write_text("base\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("checkout", "--orphan", "unrelated")
            git("rm", "-rqf", ".")
            (root / "other.txt").write_text("unrelated\n")
            git("add", ".")
            git("commit", "-qm", "other")
            head = git("rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "not descended"):
                fleet.normalize_local_commits(root, base)
            self.assertEqual(git("rev-parse", "HEAD"), head)

    def test_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev=fleet.create_failure_evidence(BASE,tmp,1,1)
            self.assertEqual(ev["outcome"],"FAILED")
            self.assertNotIn(BASE["prompt"],(Path(tmp)/"result.json").read_text())
    def test_scope_failure_always_records_evidence(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            target=root/"target"
            target.mkdir()
            output=root/"evidence"
            with patch.dict(os.environ, {
                "FLEET_MANIFEST_JSON": json.dumps(BASE),
                "FLEET_RESULT": str(output),
                "FLEET_TARGET": str(target),
                "FLEET_AGENT_OK": "true",
                "GITHUB_RUN_ID": "123",
                "GITHUB_RUN_ATTEMPT": "1",
            }):
                import sys
                with patch.object(sys, "argv", ["oc_fleet_contract.py", "gate"]):
                    with self.assertRaisesRegex(ValueError, "failure evidence recorded"):
                        fleet.cli()
            evidence=json.loads((output/"result.json").read_text())
            self.assertEqual(evidence["outcome"], "FAILED")
            self.assertIsNone(evidence["patch_sha256"])
            self.assertFalse((output/"changes.patch").exists())

    def test_curated_optional_integrations(self):
        for integrations in ([], ["codacy"], ["sonarqube","context7"], ["codacy","context7","sonarqube"]):
            m=parse({"integrations":integrations})
            self.assertEqual(m["integrations"], integrations)
        for invalid in (["arbitrary-mcp"], ["codacy","codacy"], [1], "codacy", ["sonarqube"]*4):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                parse({"integrations":invalid})

    def test_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*a): subprocess.run(["git","-C",str(root),*a],check=True,stdout=subprocess.DEVNULL)
            git("init","-q");git("config","user.name","Fleet");git("config","user.email","fleet@example.invalid")
            (root/"playground/fleet").mkdir(parents=True)
            (root/"playground/fleet/a.txt").write_text("old\n")
            git("add",".");git("commit","-qm","base")
            (root/"playground/fleet/link.txt").symlink_to("a.txt")
            with self.assertRaises(ValueError): fleet.scoped_patch(root,BASE["allowed_paths"])
if __name__=="__main__": unittest.main(verbosity=2)
