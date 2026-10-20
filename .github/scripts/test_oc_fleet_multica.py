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
