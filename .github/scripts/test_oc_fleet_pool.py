"""Negative-first authorized worker pool tests, no real tokens or GH writes."""
import json
import subprocess
import unittest
from unittest.mock import patch

import oc_fleet_pool as pool


class WorkerPoolTests(unittest.TestCase):
    def test_registry_default_and_second_account(self):
        done=subprocess.CompletedProcess([],0,stdout="MakerParsDev/oc-test,boardreadyops/oc-test\n")
        with patch.object(pool.subprocess,"run",return_value=done) as call:
            self.assertEqual(pool.registered(),
                ["MakerParsDev/oc-test","boardreadyops/oc-test"])
            self.assertNotIn("GH_TOKEN",call.call_args.kwargs["env"])
            self.assertNotIn("GITHUB_TOKEN",call.call_args.kwargs["env"])

    def test_registry_rejects_unknown_or_missing_default(self):
        for raw in ("", "boardreadyops/oc-test", "MakerParsDev/oc-test,",
                    "MakerParsDev/oc-test,MakerParsDev/oc-test",
                    "MakerParsDev/oc-test,../../other",
                    "MakerParsDev/oc-test,"+"x/o,"*10):
            proc=subprocess.CompletedProcess([],0,stdout=raw)
            with self.subTest(raw=raw), patch.object(pool.subprocess,"run",
                                         return_value=proc):
                with self.assertRaises(ValueError):
                    pool.registered()
        failure=subprocess.CompletedProcess([],1,stdout="")
        with patch.object(pool.subprocess,"run",return_value=failure):
            with self.assertRaisesRegex(ValueError,"unavailable"):
                pool.registered()

    def test_each_account_identity_and_workflow_required(self):
        events=[]
        def fake_identity(user):
            events.append(("identity",user))
            return {"account":user}
        def fake_authorized(env,name):
            events.append(("allowlist",env["account"],name))
            return name
        def fake_gh(env,url):
            events.append(("counts",env["account"],url))
            return {"total_count":0,"workflow_runs":[]}
        with patch.object(pool.worker,"identity",side_effect=fake_identity),\
             patch.object(pool.worker,"authorized_worker_repo",
                          side_effect=fake_authorized),\
             patch.object(pool.worker,"gh",side_effect=fake_gh):
            v=pool.probe("boardreadyops/oc-test")
            self.assertEqual(v["account"],"boardreadyops")
            self.assertEqual(v["busy"],0)
            self.assertEqual(events[0],("identity","boardreadyops"))
            self.assertEqual(events[1][0],"allowlist")
            self.assertEqual(len([e for e in events if e[0]=="counts"]),len(pool.ACTIVE_STATES))

    def test_unknown_count_never_opens_capacity(self):
        with patch.object(pool.worker,"identity",return_value={}),\
             patch.object(pool.worker,"authorized_worker_repo",
                          return_value="MakerParsDev/oc-test"),\
             patch.object(pool.worker,"gh",return_value={
                            "total_count":101,"workflow_runs":[{}]*100}):
            with self.assertRaisesRegex(ValueError,"Incomplete"):
                pool.probe("MakerParsDev/oc-test")

    def test_balance_prefers_idle_account_and_global_capacity(self):
        workers=[{"repo":"MakerParsDev/oc-test","account":"MakerParsDev","busy":4},
                 {"repo":"boardreadyops/oc-test","account":"boardreadyops","busy":0}]
        with patch.object(pool,"registered",
                          return_value=[x["repo"] for x in workers]),\
             patch.object(pool,"probe",side_effect=lambda repo:next(
                 v for v in workers if v["repo"]==repo)):
            out=pool.choose("oaslananka/adxl355",99)
            self.assertEqual(out["account"],"boardreadyops")
            self.assertEqual(out["global_free"],0)
            self.assertEqual(pool.global_slots(),0)

    def test_distinct_issues_spread_on_tied_owners(self):
        workers=[{"repo":"MakerParsDev/oc-test","account":"MakerParsDev","busy":0},
                 {"repo":"boardreadyops/oc-test","account":"boardreadyops","busy":0}]
        with patch.object(pool,"registered",
                          return_value=[x["repo"] for x in workers]),\
             patch.object(pool,"probe",side_effect=lambda repo:next(
                 v for v in workers if v["repo"]==repo)):
            accounts={pool.choose("oaslananka/adxl355",i)["account"]
                      for i in range(20,40)}
            self.assertEqual(accounts,{"MakerParsDev","boardreadyops"})

    def test_retry_preserves_original_account(self):
        workers=[{"repo":"MakerParsDev/oc-test","account":"MakerParsDev","busy":0},
                 {"repo":"boardreadyops/oc-test","account":"boardreadyops","busy":2}]
        with patch.object(pool,"registered",
                          return_value=[x["repo"] for x in workers]),\
             patch.object(pool,"probe",side_effect=lambda repo:next(
                 v for v in workers if v["repo"]==repo)):
            choice=pool.choose("oaslananka/adxl355",77,
                               required_worker="boardreadyops/oc-test")
            self.assertEqual(choice["account"],"boardreadyops")
            with self.assertRaisesRegex(ValueError,"no longer approved"):
                pool.choose("oaslananka/adxl355",77,required_worker="unknown/worker")

    def test_capacity_fail_closed_when_any_account_cannot_be_counted(self):
        with patch.object(pool,"registered",
                          return_value=["MakerParsDev/oc-test","boardreadyops/oc-test"]),\
             patch.object(pool,"probe",side_effect=[
                  {"repo":"MakerParsDev/oc-test","account":"MakerParsDev","busy":0},
                  ValueError("missing secondary account token")]):
            with self.assertRaisesRegex(ValueError,"missing secondary"):
                pool.snapshot()


if __name__ == "__main__":
    unittest.main()
