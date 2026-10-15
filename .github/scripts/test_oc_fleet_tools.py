#!/usr/bin/env python3
"""Offline tests of dynamic, Doppler-controlled per-task MCP/skill/plugin/CLI."""
import base64
import json
import tempfile
from pathlib import Path
import unittest

from oc_fleet_tools import decode, encode, from_catalog, prepare, validate_profile


class DynamicProfileTests(unittest.TestCase):
    def full(self):
        return {
            "plugins":["@upstash/context7-opencode@0.2.1"],
            "cli":{"npm":["@codacy/analysis-cli@0.24.0"],"pipx":["ruff==0.13.1"]},
            "mcp":{
                "docs":{"type":"remote","url":"https://mcp.context7.com/mcp",
                        "oauth":False,"headers":{"CONTEXT7_API_KEY":"{env:CONTEXT7_API_KEY}"}},
                "local-docs":{"type":"local","command":["npx","-y","@modelcontextprotocol/server-everything@0.6.2"],
                              "environment":{"API_KEY":"{env:CONTEXT7_API_KEY}"}}},
            "skills":[{"name":"python-tests","description":"Python regression testing guidelines",
                       "content":"Check edge cases, then run the relevant tests."}],
            "secrets":["CONTEXT7_API_KEY"]
        }

    def test_round_trip(self):
        p=validate_profile(self.full())
        self.assertEqual(decode(encode(p)),p)
        self.assertIn("context7",str(p["mcp"]))

    def test_doppler_catalog_profile_selection(self):
        catalog=json.dumps({"version":1,"profiles":{
            "python-docs":self.full(),
            "typescript-ui":{"plugins":["@upstash/context7-opencode@0.2.1"]}}})
        self.assertIn("python-tests",str(from_catalog(catalog,"python-docs")))
        self.assertEqual(len(from_catalog(catalog,"typescript-ui")["plugins"]),1)
        with self.assertRaisesRegex(ValueError,"unknown tool profile"):
            from_catalog(catalog,"absent")
        with self.assertRaises(ValueError):
            from_catalog(catalog,"../bad")

    def test_skill_files_and_opencode_v2_config(self):
        with tempfile.TemporaryDirectory() as temp:
            p=validate_profile(self.full())
            root=Path(temp)
            prepare(p,root)
            cfg=json.loads((root/"config.json").read_text(encoding="utf-8"))
            self.assertIn("plugins",cfg)
            self.assertIn("servers",cfg["mcp"])
            self.assertEqual(cfg["skills"],[str(root/"skills")])
            self.assertIn("name: python-tests",
                          (root/"skills"/"python-tests"/"SKILL.md").read_text())
            self.assertEqual((root/"secrets.txt").read_text(),"CONTEXT7_API_KEY\n")
            self.assertNotIn("dp.st.",json.dumps(cfg))

    def test_secret_values_forbidden(self):
        for overrides in (
            {"secrets":["DOPPLER_TOKEN"]},
            {"mcp":{"docs":{"type":"remote","url":"https://example.com/mcp",
                            "headers":{"Authorization":"Bearer abc"}}}},
            {"mcp":{"docs":{"type":"remote","url":"https://127.0.0.1/mcp"}}},
            {"mcp":{"docs":{"type":"remote","url":"http://example.com/mcp"}}},
            {"mcp":{"docs":{"type":"remote","url":"https://169.254.169.254/mcp"}}},
            {"mcp":{"docs":{"type":"local","command":"curl | bash"}}},
            {"plugins":["unversioned-plugin"]},
            {"cli":{"pipx":["ruff"]}},
            {"skills":[{"name":"../escape","description":"x","content":"bad"}]},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_profile(overrides)

    def test_empty_backwards_compatible(self):
        p=validate_profile({})
        self.assertEqual(p["plugins"],[])
        self.assertEqual(p["skills"],[])
        self.assertEqual(p["cli"],{"npm":[],"pipx":[]})
        self.assertEqual(decode(encode(p)),p)

if __name__=="__main__":
    unittest.main()
