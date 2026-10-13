#!/usr/bin/env python3
"""Offline SQLite repair queue regression and concurrent reservation tests."""
import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from ocfilo_repair_queue import connect, reserve, acknowledge, finalize

SHA="a"*40
def decision(sha=SHA,key="b"*20):
    return {"repository":"oaslananka-dev/oc-test","pr_number":1,"head_sha":sha,
            "state":"REPAIR_REQUIRED","dispatch_allowed":True,"repair_key":key}
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"queue.db")
        self.con=connect(self.db)
        self.addCleanup(self.con.close)
    def test_reserve(self):
        r=reserve(self.con,decision(),SHA,"github:one")
        self.assertEqual(r["outcome"],"RESERVED")
        self.assertEqual(r["job"]["attempt"],1)
    def test_duplicate_delivery(self):
        reserve(self.con,decision(),SHA,"github:one")
        self.assertEqual(reserve(self.con,decision(),SHA,"github:one")["outcome"],"DUPLICATE_DELIVERY")
    def test_reused_delivery_changed_task_denied(self):
        reserve(self.con,decision(),SHA,"github:one")
        with self.assertRaises(ValueError):
            reserve(self.con,decision(key="c"*20),SHA,"github:one")
    def test_single_writer(self):
        reserve(self.con,decision(),SHA,"github:one")
        self.assertEqual(reserve(self.con,decision(),"a"*40,"github:two")["outcome"],"ALREADY_ACTIVE")
    def test_stale_head(self):
        with self.assertRaises(ValueError):reserve(self.con,decision(),"c"*40,"github:one")
    def test_green_decision(self):
        d=decision();d["state"]="READY_FOR_MERGE"
        with self.assertRaises(ValueError):reserve(self.con,d,SHA,"github:one")
    def test_ack_and_finalize(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        a=acknowledge(self.con,j["id"],101,SHA)
        self.assertEqual(a["state"],"DISPATCHED")
        f=finalize(self.con,j["id"],101,SHA,"SUCCEEDED")
        self.assertEqual(f["state"],"SUCCEEDED")
    def test_duplicate_ack_denied(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        acknowledge(self.con,j["id"],101,SHA)
        with self.assertRaises(ValueError):acknowledge(self.con,j["id"],101,SHA)
    def test_wrong_workflow_denied(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        acknowledge(self.con,j["id"],101,SHA)
        with self.assertRaises(ValueError):finalize(self.con,j["id"],102,SHA,"SUCCEEDED")
    def test_wrong_sha_finalize_denied(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        acknowledge(self.con,j["id"],101,SHA)
        with self.assertRaises(ValueError):finalize(self.con,j["id"],101,"c"*40,"SUCCEEDED")
    def test_no_finalize_before_ack(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        with self.assertRaises(ValueError):finalize(self.con,j["id"],101,SHA,"SUCCEEDED")
    def test_limit_three(self):
        for i in range(3):
            j=reserve(self.con,decision(key=f"{i:020x}"),SHA,f"gh:{i:05d}")["job"]
            acknowledge(self.con,j["id"],101+i,SHA)
            finalize(self.con,j["id"],101+i,SHA,"FAILED")
        self.assertEqual(reserve(self.con,decision(key="f"*20),SHA,"gh:new01")["outcome"],"NEEDS_HUMAN")
    def test_repaired_key_no_repeat(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        acknowledge(self.con,j["id"],101,SHA)
        finalize(self.con,j["id"],101,SHA,"SUCCEEDED")
        self.assertEqual(reserve(self.con,decision(),SHA,"github:two")["outcome"],"ALREADY_REPAIRED")
    def test_failed_key_explicit_retry(self):
        j=reserve(self.con,decision(),SHA,"github:one")["job"]
        acknowledge(self.con,j["id"],101,SHA)
        finalize(self.con,j["id"],101,SHA,"FAILED")
        self.assertEqual(reserve(self.con,decision(),SHA,"github:two")["job"]["attempt"],2)
    def test_second_isolated_f1_pr(self):
        d=decision()
        d["pr_number"]=2
        result=reserve(self.con,d,SHA,"github:pr2-live")
        self.assertEqual(result["outcome"],"RESERVED")
        self.assertEqual(result["job"]["pr_number"],2)

    def test_persist_restart(self):
        r=reserve(self.con,decision(),SHA,"github:one")
        c=connect(self.db)
        try:self.assertEqual(reserve(c,decision(),SHA,"github:one")["job"]["id"],r["job"]["id"])
        finally:c.close()
    def test_concurrent_reservations(self):
        def claim(i):
            c=connect(self.db)
            try:return reserve(c,decision(),SHA,f"concurrent:{i:04d}")["outcome"]
            finally:c.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(claim,range(6)))
        self.assertEqual(results.count("RESERVED"),1)
        self.assertEqual(results.count("ALREADY_ACTIVE"),5)

if __name__=="__main__":
    unittest.main()
