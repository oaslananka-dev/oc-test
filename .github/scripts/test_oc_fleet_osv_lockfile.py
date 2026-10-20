"""Isolated classifier and metadata-preserving lockfile replay regression tests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_osv_lockfile as osv

FINDING={
 "package":"source-map-js","old":"1.2.1",
 "cve":"CVE-2026-93749","ghsa":"GHSA-68fv-2mgg-jv7q"}
SARIF={"runs":[{"results":[{
    "ruleId":FINDING["cve"],
    "message":{"text":"Package 'source-map-js@1.2.1' is vulnerable to "
             "'CVE-2026-93749' (also known as 'GHSA-68fv-2mgg-jv7q')."},
    "locations":[{"physicalLocation":{"artifactLocation":{
        "uri":"node/package-lock.json"}}}],
}]}]}
ADVISORY={
 "ghsa_id":FINDING["ghsa"],"type":"reviewed",
 "identifiers":[{"type":"CVE","value":FINDING["cve"]}],
 "severity":"high","withdrawn_at":None,
 "vulnerabilities":[{
    "package":{"ecosystem":"npm","name":"source-map-js"},
    "first_patched_version":"1.2.2",
    "vulnerable_version_range":">= 1.0.0, < 1.2.2",
 }]
}
BEFORE={
 "lockfileVersion":3,"name":"adxl355","version":"0.1.0",
 "packages":{
    "":{"name":"adxl355","version":"0.1.0","devDependencies":{"vitest":"5.0.0"}},
    "node_modules/source-map-js":{
       "version":"1.2.1",
       "resolved":"https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.1.tgz",
       "integrity":"sha512-oldhash","dev":True,"license":"BSD-3-Clause"},
    "node_modules/vitest":{
       "version":"5.0.0","resolved":"https://registry.npmjs.org/vitest/-/vitest-5.0.0.tgz",
       "integrity":"sha512-other","dev":True}
 }
}
AFTER=deepcopy(BEFORE)
AFTER["packages"]["node_modules/source-map-js"].update({
 "version":"1.2.2",
 "resolved":"https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz",
 "integrity":"sha512-newhash"})


class OsvalTests(unittest.TestCase):
    def test_structured_sarif_and_validated_advisory(self):
        self.assertEqual(osv.one_finding(SARIF),FINDING)
        self.assertEqual(osv.validated_advisory(FINDING,ADVISORY),"1.2.2")
        self.assertTrue(osv.validate_replay(BEFORE,AFTER,FINDING,"1.2.2"))

    def test_reject_non_lockfile_and_multiple_advisories(self):
        cases=[]
        a=deepcopy(SARIF);a["runs"][0]["results"][0]["locations"][0][
            "physicalLocation"]["artifactLocation"]["uri"]="python/requirements.lock"
        cases.append(a)
        a=deepcopy(SARIF);a["runs"][0]["results"].append(deepcopy(SARIF["runs"][0]["results"][0]))
        cases.append(a)
        a=deepcopy(SARIF);a["runs"][0]["results"][0]["ruleId"]="CVE-2026-00000"
        cases.append(a)
        a=deepcopy(SARIF);a["runs"][0]["results"][0]["message"]["text"]="evil shell command"
        cases.append(a)
        for value in cases:
            with self.subTest(value=value),self.assertRaises(ValueError):
                osv.one_finding(value)

    def test_reject_wrong_github_advisory_package_or_withdrawn(self):
        for modified in (
            {"ghsa_id":"GHSA-aaaa-bbbb-cccc"},
            {"severity":"low"},
            {"type":"unreviewed"},
            {"identifiers":[]},
            {"withdrawn_at":"2026-10-06"},
            {"vulnerabilities":[]},
            {"vulnerabilities":[{"package":{"ecosystem":"PyPI","name":"source-map-js"},
                                  "first_patched_version":"1.2.2"}]},
            {"vulnerabilities":[{"package":{"ecosystem":"npm","name":"source-map-js"},
                                  "first_patched_version":"2.0.0"}]},
            {"vulnerabilities":[{"package":{"ecosystem":"npm","name":"source-map-js"},
                                  "first_patched_version":"1.3.0"}]},
            {"vulnerabilities":[{"package":{"ecosystem":"npm","name":"source-map-js"},
                                  "first_patched_version":"1.2.1"}]},
        ):
            with self.subTest(modified=modified),self.assertRaises(ValueError):
                osv.validated_advisory(FINDING,{**ADVISORY,**modified})

    def test_lockfile_scope_unchanged_dependency_tree_required(self):
        for mutation in (
            lambda x:x["packages"]["node_modules/vitest"].update({"version":"5.0.1"}),
            lambda x:x["packages"]["node_modules/source-map-js"].update({"dev":False}),
            lambda x:x.update({"version":"1.1.0"}),
            lambda x:x["packages"].update({"node_modules/added":{"version":"0.1.0"}}),
            lambda x:x["packages"]["node_modules/source-map-js"].update({"resolved":"https://evil.example/x.tgz"}),
            lambda x:x["packages"]["node_modules/source-map-js"].update({"integrity":"bad"}),
            lambda x:x["packages"]["node_modules/source-map-js"].update({"version":"1.2.3"}),
        ):
            value=deepcopy(AFTER)
            mutation(value)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                osv.validate_replay(BEFORE,value,FINDING,"1.2.2")

    def test_requires_reported_vulnerable_dev_version(self):
        for mutation in (
            lambda x:x["packages"]["node_modules/source-map-js"].update({"version":"1.2.2"}),
            lambda x:x["packages"]["node_modules/source-map-js"].update({"dev":False}),
            lambda x:x.update({"lockfileVersion":2}),
        ):
            value=deepcopy(BEFORE)
            mutation(value)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                osv.locked_package(value,FINDING)

    def test_safe_local_readonly_plan_and_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)
            (p/".git").mkdir()
            (p/"node").mkdir()
            (p/"node/package.json").write_text('{"name":"adxl355"}')
            (p/"node/package-lock.json").write_text(json.dumps(BEFORE))
            class Completed:
                stdout=""
            with patch.object(osv.subprocess,"run",return_value=Completed()) as run:
                got=osv.prepare(p,SARIF,ADVISORY)
                self.assertEqual(got[1],"1.2.2")
                self.assertEqual(got[2],BEFORE)
                run.assert_called_once()
            (p/"node/package-lock.json").write_text(json.dumps(AFTER))
            with patch.object(osv.subprocess,"run",return_value=Completed()):
                with self.assertRaisesRegex(ValueError,"reported vulnerable"):
                    osv.prepare(p,SARIF,ADVISORY)

    def test_advisory_id_is_allowlisted_format(self):
        with self.assertRaisesRegex(ValueError,"Invalid GHSA"):
            osv.gh_advisory("GHSA-aaaa-bbbb-cccc; echo hi")


if __name__=="__main__":
    unittest.main()
