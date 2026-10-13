#!/usr/bin/env python3
"""Regression tests for artifact handoff provenance and duplicate replay."""
import copy
import tempfile
import unittest
from pathlib import Path
from ocfilo_handoff import validate, remember

SHA = "a"*40
RUN_ID = 123456

def fixtures():
    decision = {
        "repository":"oaslananka-dev/oc-test", "pr_number":2,
        "head_sha":SHA,"state":"READY_FOR_MERGE","dispatch_allowed":False,
        "repair_key":None,"failing_checks":[],"pending_checks":[],
        "missing_checks":[],"blocking_reviews":[],"bot_feedback":[],
    }
    run = {
        "id":RUN_ID,"repository":{"full_name":"oaslananka-dev/oc-test"},
        "path":".github/workflows/ocfilo-pr-observer.yml",
        "event":"workflow_run","status":"completed","conclusion":"success",
    }
    artifact = {
        "name":f"ocfilo-pr-observer-{RUN_ID}-2", "expired":False,
        "workflow_run":{"id":RUN_ID},
    }
    return decision,copy.deepcopy(decision),run,artifact


class HandoffTests(unittest.TestCase):
    def validate(self, edit=None):
        a,b,c,d=fixtures()
        if edit:
            edit(a,b,c,d)
        return validate(a,b,c,d,2)

    def test_valid(self):
        self.assertEqual(self.validate()["outcome"],"VERIFIED_READ_ONLY")
    def test_stale_sha(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: b.update(head_sha="b"*40))
    def test_tampered_decision(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: a.update(state="REPAIR_REQUIRED"))
    def test_unsafe_dispatch(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: a.update(dispatch_allowed=True))
    def test_wrong_run(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: c.update(id=654321))
    def test_failed_run(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: c.update(conclusion="failure"))
    def test_wrong_workflow(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: c.update(path=".github/workflows/arbitrary.yml"))
    def test_wrong_repo(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: c["repository"].update(full_name="attacker/repo"))
    def test_forged_artifact(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: d.update(name="ocfilo-pr-observer-123456-1"))
    def test_expired(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: d.update(expired=True))
    def test_check_disagreement(self):
        with self.assertRaises(ValueError):
            self.validate(lambda a,b,c,d: b["failing_checks"].append("fixture-quality"))
    def test_valid_repair(self):
        a,b,c,d=fixtures()
        for obj in (a,b):
            obj.update(state="REPAIR_REQUIRED",dispatch_allowed=True,repair_key="b"*20,
                       failing_checks=["fixture-quality"])
        self.assertTrue(validate(a,b,c,d,2)["dispatch_allowed"])
    def test_repair_without_failing_check(self):
        a,b,c,d=fixtures()
        for obj in (a,b):
            obj.update(state="REPAIR_REQUIRED",dispatch_allowed=True,repair_key="b"*20)
        with self.assertRaises(ValueError):validate(a,b,c,d,2)
    def test_durable_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/"ledger.sqlite")
            result=self.validate()
            self.assertEqual(remember(db,result)["outcome"],"VERIFIED_READ_ONLY")
            self.assertEqual(remember(db,result)["outcome"],"DUPLICATE_VERIFIED_DELIVERY")
    def test_conflicting_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/"ledger.sqlite")
            result=self.validate()
            remember(db,result)
            with self.assertRaises(ValueError):remember(db,dict(result,state="REPAIR_REQUIRED"))

if __name__ == "__main__":
    unittest.main()
