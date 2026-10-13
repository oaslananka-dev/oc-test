#!/usr/bin/env python3
"""Recovery drill: durable verified handoff -> repair queue, without GitHub writes.

Simulate a crash AFTER accepting a GitHub observer artifact but BEFORE queue
reservation. Replayed delivery must still reserve exactly one SHA-bound job.
"""
import tempfile
import unittest
from pathlib import Path

from ocfilo_handoff import remember, validate
from ocfilo_repair_queue import connect, reserve, acknowledge, finalize

REPO="oaslananka-dev/oc-test"
SHA="a"*40
KEY="b"*20
RUN=431234

def evidence():
    decision=dict(repository=REPO,pr_number=2,head_sha=SHA,
                  state="REPAIR_REQUIRED",dispatch_allowed=True,
                  repair_key=KEY,failing_checks=["fixture-quality"],
                  missing_checks=[],pending_checks=[],blocking_reviews=[],bot_feedback=[])
    metadata=dict(id=RUN,repository=dict(full_name=REPO),
                  path=".github/workflows/ocfilo-pr-observer.yml",
                  event="workflow_run",status="completed",conclusion="success")
    artifact=dict(name=f"ocfilo-pr-observer-{RUN}-2",expired=False,
                  workflow_run=dict(id=RUN))
    return decision,metadata,artifact

class Recovery(unittest.TestCase):
    def test_crash_after_handoff_verification_keeps_repair_replayable(self):
        decision,run,artifact=evidence()
        with tempfile.TemporaryDirectory() as temp:
            handoff_db=str(Path(temp)/"deliveries.sqlite3")
            queue_db=str(Path(temp)/"repairs.sqlite3")
            first=validate(decision,decision,run,artifact,2)
            self.assertEqual(remember(handoff_db,first)["outcome"],"VERIFIED_READ_ONLY")
            # Process dies without reserving a worker. Later delivery is duplicate.
            replay=validate(decision,decision,run,artifact,2)
            self.assertEqual(remember(handoff_db,replay)["outcome"],"DUPLICATE_VERIFIED_DELIVERY")
            con=connect(queue_db)
            try:
                receipt=reserve(con,decision,SHA,f"reconcile:pr2:{KEY}")
                self.assertEqual(receipt["outcome"],"RESERVED")
                duplicate=reserve(con,decision,SHA,f"reconcile:pr2:{KEY}")
                self.assertEqual(duplicate["outcome"],"DUPLICATE_DELIVERY")
                self.assertEqual(duplicate["job"]["id"],receipt["job"]["id"])
                acknowledged=acknowledge(con,receipt["job"]["id"],831234,SHA)
                self.assertEqual(acknowledged["state"],"DISPATCHED")
                completed=finalize(con,receipt["job"]["id"],831234,SHA,"SUCCEEDED")
                self.assertEqual(completed["state"],"SUCCEEDED")
                self.assertEqual(reserve(con,decision,SHA,"other:delivery")["outcome"],
                                 "ALREADY_REPAIRED")
            finally:
                con.close()

    def test_stale_artifact_does_not_reserve(self):
        decision,run,artifact=evidence()
        current=dict(decision,head_sha="c"*40)
        with self.assertRaises(ValueError):
            validate(decision,current,run,artifact,2)

    def test_changed_state_does_not_reserve(self):
        decision,run,artifact=evidence()
        current=dict(decision,state="READY_FOR_MERGE",dispatch_allowed=False,
                     repair_key=None,failing_checks=[])
        with self.assertRaises(ValueError):
            validate(decision,current,run,artifact,2)

    def test_untrusted_run_rejected(self):
        decision,run,artifact=evidence()
        run["path"]=".github/workflows/unrelated.yml"
        with self.assertRaises(ValueError):
            validate(decision,decision,run,artifact,2)

if __name__=="__main__":
    unittest.main()
