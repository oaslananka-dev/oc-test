#!/usr/bin/env python3
"""Offline checks for Multica's model-free Github Actions handoff."""
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

source=Path(__file__).with_name("oc_fleet_multica.py")
spec=importlib.util.spec_from_file_location("handoff",source)
handoff=importlib.util.module_from_spec(spec)
sys.modules["handoff"]=handoff
spec.loader.exec_module(handoff)

KEY="OAS-14"
PID="0506e0e5-01a7-4de9-b63b-67e993809721"
META={
    "fleet_target_repo":"oaslananka-dev/oc-test",
    "fleet_target_issue":"53",
    "fleet_worker_repo":"MakerParsDev/oc-test",
    "fleet_worker_account":"MakerParsDev",
    "fleet_target_account":"oaslananka",
    "fleet_allowed_path":"playground/fleet/supplemental-receipt.json",
    "fleet_ready":False
}


def record(**updates):
    return {"identifier":KEY,"project_id":PID,"status":"backlog",
            "assignee_type":None,"metadata":{**META,**updates}}


class HandoffTests(unittest.TestCase):
    def test_plan_is_readonly_and_has_correct_identity(self):
        meta=handoff.validate_issue(record(),KEY,PID,False)
        args=handoff.controller_command(KEY,meta,False)
        self.assertEqual(args[-1],"--dry-run")
        self.assertIn("MakerParsDev/oc-test",args)
        self.assertEqual(args[args.index("--multica-issue")+1],KEY)

    def test_safe_base_advance_requires_external_pr_only_without_automerge(self):
        opts={"fleet_target_repo":"oaslananka/adxl355",
              "fleet_external_pr_only":True,
              "fleet_auto_merge":False,
              "fleet_allow_safe_base_advance":True}
        meta=handoff.validate_issue(record(**opts),KEY,PID,False)
        args=handoff.controller_command(KEY,meta,True)
        self.assertNotIn("--allow-safe-base-advance",args)
        for change in ({"fleet_external_pr_only":False},
                       {"fleet_auto_merge":True},
                       {"fleet_allow_safe_base_advance":"true"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                handoff.validate_issue(record(**{**opts,**change}),KEY,PID,False)

    def test_safe_base_policy_is_selected_at_dispatch_and_not_implicitly_enabled(self):
        options={"fleet_target_repo":"oaslananka/adxl355",
                 "fleet_external_pr_only":True,
                 "fleet_auto_merge":False,
                 "fleet_allow_safe_base_advance":True}
        authorized=handoff.validate_issue(record(**options),KEY,PID,False)
        argv=handoff.controller_command(KEY,authorized,True)
        self.assertIn("--safe-base-advance-policy",argv)
        self.assertNotIn("--allow-safe-base-advance",argv)
        default=handoff.controller_command(KEY,record(**{
            "fleet_external_pr_only":True, "fleet_auto_merge":False
        })["metadata"],True)
        self.assertNotIn("--safe-base-advance-policy",default)
        self.assertNotIn("--allow-safe-base-advance",default)

    def test_run_requires_operator_ready_flag(self):
        with self.assertRaisesRegex(ValueError,"fleet_ready is false"):
            handoff.validate_issue(record(),KEY,PID,True)
        meta=handoff.validate_issue(record(fleet_ready=True),KEY,PID,True)
        self.assertNotIn("--dry-run",handoff.controller_command(KEY,meta,True))

    def test_project_and_status_cannot_be_switched(self):
        with self.assertRaisesRegex(ValueError,"mismatch"):
            handoff.validate_issue({**record(),"project_id":"f"*36},KEY,PID,False)
        for s in ("blocked","cancelled","done"):
            with self.assertRaisesRegex(ValueError,"not dispatchable"):
                handoff.validate_issue({**record(),"status":s},KEY,PID,False)

    def test_no_native_agent_execution(self):
        with self.assertRaisesRegex(ValueError,"native"):
            handoff.validate_issue({**record(),"assignee_type":"agent"},KEY,PID,False)

    def test_metadata_scope_type_validation(self):
        variants=[
            {"fleet_allowed_path":"../../secret"},
            {"fleet_allowed_path":".github/workflows/test.yml"},
            {"fleet_target_issue":"00053"},
            {"fleet_worker_repo":"evil_repo"},
            {"fleet_target_repo":"../../"},
            {"fleet_ready":"true"},
            {"fleet_worker_account":"bad space"},
        ]
        for v in variants:
            with self.subTest(v=v),self.assertRaises(ValueError):
                handoff.validate_issue(record(**v),KEY,PID,False)

    def test_multirepo_scope_explicit_and_no_auto_merge(self):
        meta=handoff.validate_issue(record(
            fleet_target_repo="oaslananka/adxl355",
            fleet_target_issue="124",
            fleet_allowed_path="python/tests/test_spidev_adapter.py",
            fleet_allowed_paths=["python/src/adxl355/adapters/spidev.py"],
            fleet_external_pr_only=True,
            fleet_auto_merge=False,
            fleet_ready=True,
        ),KEY,PID,True)
        cmd=handoff.controller_command(KEY,meta,True)
        self.assertEqual(cmd.count("--path"),2)
        self.assertIn("--external-pr-only",cmd)
        self.assertIn("--require-approved-target",cmd)
        for updates in (
            {"fleet_target_repo":"oaslananka/adxl355"},
            {"fleet_target_repo":"oaslananka/adxl355","fleet_external_pr_only":True,
             "fleet_auto_merge":True},
            {"fleet_allowed_path":".github/workflows/build.yml"},
            {"fleet_allowed_path":"python/../secrets"},
            {"fleet_allowed_path":"python/tests/test_spidev_adapter.py", "fleet_allowed_paths":["python/tests/test_spidev_adapter.py"]},
            {"fleet_allowed_paths":["python/tests/","python/tests/test_spidev_adapter.py"]},
        ):
            with self.subTest(updates=updates),self.assertRaises(ValueError):
                handoff.validate_issue(record(**updates),KEY,PID,False)

    def test_multica_primitive_string_roundtrips_as_two_exact_scopes(self):
        stored='["python/src/adxl355/adapters/spidev.py"]'
        issue=record(
            fleet_target_repo="oaslananka/adxl355",
            fleet_target_issue="130",
            fleet_allowed_path="python/tests/test_spidev_adapter.py",
            fleet_allowed_paths=stored,
            fleet_external_pr_only=True,
            fleet_auto_merge=False,
            fleet_ready=True,
        )
        meta=handoff.validate_issue(issue,KEY,PID,True)
        command=handoff.controller_command(KEY,meta,True)
        paths=[command[i+1] for i,x in enumerate(command) if x=="--path"]
        self.assertEqual(paths,[
            "python/tests/test_spidev_adapter.py",
            "python/src/adxl355/adapters/spidev.py"])
        self.assertIn("--external-pr-only",command)
        self.assertNotIn("--dry-run",command)
        # The Multica source remains scalar and is not silently rewritten.
        self.assertEqual(meta["fleet_allowed_paths"],stored)

    def test_encoded_extra_scopes_fail_closed(self):
        cases=[
            "not JSON",
            '"python/src/adxl355/adapters/spidev.py"',
            'null',
            'true',
            '{}',
            '[".github/workflows/ci.yml"]',
            '["python/../sensitive"]',
            '["python/tests/test_spidev_adapter.py"]',
            '["python/tests/","python/tests/test_spidev_adapter.py"]',
            '["python/src/adxl355/adapters/spidev.py",1]',
            '["python/src/adxl355/adapters/spidev.py",]',
            '["python/src/adxl355/adapters/spidev.py"]' * 300,
        ]
        for value in cases:
            with self.subTest(value=value[:50]),self.assertRaises(ValueError):
                handoff.validate_issue(record(
                    fleet_allowed_path="python/tests/test_spidev_adapter.py",
                    fleet_allowed_paths=value),KEY,PID,False)

    def test_task_selects_approved_model_profile_tools_and_integrations(self):
        meta=handoff.validate_issue(record(
            fleet_model="opencode/nemotron-3-ultra-free",
            fleet_profile="python-ruff",
            fleet_agent="python-backend",
            fleet_tool_profile="python-docs",
            fleet_integrations='["context7","codacy"]',
            fleet_ready=True,
        ),KEY,PID,True)
        command=handoff.controller_command(KEY,meta,True)
        def value(flag):
            return command[command.index(flag)+1]
        self.assertEqual(value("--model"),"opencode/nemotron-3-ultra-free")
        self.assertEqual(value("--profile"),"python-ruff")
        self.assertEqual(value("--agent"),"python-backend")
        self.assertEqual(value("--tool-profile"),"python-docs")
        self.assertEqual([command[i+1] for i,v in enumerate(command)
                          if v=="--integration"],["context7","codacy"])
        self.assertNotIn("--dry-run",command)
        self.assertEqual(meta["fleet_integrations"],'["context7","codacy"]')

    def test_default_task_profiles_are_backward_compatible(self):
        meta=handoff.validate_issue(record(fleet_ready=True),KEY,PID,True)
        args=handoff.controller_command(KEY,meta,True)
        self.assertEqual(args[args.index("--model")+1],"opencode/big-pickle")
        self.assertEqual(args[args.index("--profile")+1],"base")
        self.assertEqual(args[args.index("--agent")+1],"build")
        self.assertNotIn("--tool-profile",args)
        self.assertNotIn("--integration",args)

    def test_task_rejects_paid_unknown_or_unbounded_profiles(self):
        variants=[
            {"fleet_model":"openai/gpt-5"},
            {"fleet_model":"opencode/big-pickle-plus"},
            {"fleet_model":7},
            {"fleet_profile":"admin"},
            {"fleet_agent":"plan"},
            {"fleet_agent":"build --model paid"},
            {"fleet_agent":7},
            {"fleet_profile":"base;evil"},
            {"fleet_tool_profile":"../../injection"},
            {"fleet_tool_profile":"python-docs --model paid"},
            {"fleet_tool_profile":4},
            {"fleet_integrations":"codacy,context7"},
            {"fleet_integrations":'["codacy","codacy"]'},
            {"fleet_integrations":'["arbitrary-tool"]'},
            {"fleet_integrations":'["codacy",1]'},
            {"fleet_integrations":'{"codacy":true}'},
            {"fleet_integrations":'"codacy"'},
            {"fleet_integrations":"null"},
            {"fleet_integrations":"[]junk"},
            {"fleet_integrations":"["*300},
            {"fleet_integrations":["codacy","codacy"]},
        ]
        for fields in variants:
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                handoff.validate_issue(record(**fields),KEY,PID,False)

    def test_integrations_allow_only_known_identifiers_for_external_pr(self):
        meta=handoff.validate_issue(record(
            fleet_target_repo="oaslananka/adxl355",
            fleet_external_pr_only=True,
            fleet_auto_merge=False,
            fleet_model="opencode/ling-3.1-flash-free",
            fleet_profile="security-semgrep",
            fleet_tool_profile="context7-mcp",
            fleet_integrations=["sonarqube"],
        ),KEY,PID,False)
        args=handoff.controller_command(KEY,meta,False)
        self.assertIn("--external-pr-only",args)
        self.assertEqual(args[args.index("--model")+1],
                         "opencode/ling-3.1-flash-free")
        self.assertEqual(args[args.index("--tool-profile")+1],"context7-mcp")
        self.assertEqual(args[args.index("--integration")+1],"sonarqube")

    def test_read_issue_uses_exact_json_api(self):
        class Reply:
            stdout='{"identifier":"OAS-14"}'
        with patch.object(handoff.subprocess,"run",return_value=Reply()) as run:
            result=handoff.read_issue(KEY)
            self.assertEqual(result["identifier"],KEY)
            self.assertEqual(run.call_args.args[0][:4],
                             ["multica","issue","get",KEY])


if __name__=="__main__":
    unittest.main()
