#!/usr/bin/env python3
"""Offline tests for safe and disjoint ocfilo batch plans."""
import copy
import unittest
from ocfilo_batch import validate

def sample():
    def job(suffix):
        return dict(task_id="f1-parallel-"+suffix,
            repository="oaslananka-dev/oc-test",phase="implement",
            model="opencode/big-pickle",agent="build",profile="base",
            review_profile="none",publish=False,fix_rounds=1,
            allowed_paths=["playground/parallel-"+suffix+".txt"],
            prompt="Create exactly one scoped text file: "+suffix)
    return {"batch_id":"f1-parallel","workers":[job("alpha"),job("beta")]}

class BatchTests(unittest.TestCase):
    def test_two_disjoint(self):
        a=validate(sample());self.assertEqual(a["worker_count"],2)
        self.assertEqual(len(a["path_owners"]),2)
    def test_three_disjoint(self):
        a=sample()
        z=copy.deepcopy(a["workers"][1]);z["task_id"]="f1-parallel-gamma"
        z["allowed_paths"]=["playground/gamma.txt"];a["workers"].append(z)
        self.assertEqual(validate(a)["worker_count"],3)
    def test_too_many(self):
        a=sample();a["workers"]*=2
        with self.assertRaises(ValueError):validate(a)
    def test_overlapping(self):
        a=sample();a["workers"][1]["allowed_paths"]=["playground/parallel-alpha.txt"]
        with self.assertRaises(ValueError):validate(a)
    def test_nested_conflict(self):
        a=sample();a["workers"][0]["allowed_paths"]=["playground/example"]
        a["workers"][1]["allowed_paths"]=["playground/example/nested"]
        with self.assertRaises(ValueError):validate(a)
    def test_traversal(self):
        a=sample();a["workers"][0]["allowed_paths"]=["playground/../.github/x"]
        with self.assertRaises(ValueError):validate(a)
    def test_absolute(self):
        a=sample();a["workers"][0]["allowed_paths"]=["/tmp/evil"]
        with self.assertRaises(ValueError):validate(a)
    def test_duplicate_id(self):
        a=sample();a["workers"][1]["task_id"]=a["workers"][0]["task_id"]
        with self.assertRaises(ValueError):validate(a)
    def test_mismatched_namespace(self):
        a=sample();a["workers"][0]["task_id"]="unrelated"
        with self.assertRaises(ValueError):validate(a)
    def test_reject_publish(self):
        a=sample();a["workers"][0]["publish"]=True
        with self.assertRaises(ValueError):validate(a)
    def test_unsafe_model(self):
        a=sample();a["workers"][0]["model"]="premium/api-model"
        with self.assertRaises(ValueError):validate(a)
    def test_unknown_mcp(self):
        a=sample();a["workers"][0]["mcp"]={"evil":"remote"}
        with self.assertRaises(ValueError):validate(a)
    def test_unsafe_profile(self):
        a=sample();a["workers"][0]["profile"]="exec-anything"
        with self.assertRaises(ValueError):validate(a)
    def test_codacy_scope(self):
        a=sample();a["workers"][0]["review_profile"]="codacy-local"
        with self.assertRaises(ValueError):validate(a)
    def test_codacy_python(self):
        a=sample();a["workers"][0]["review_profile"]="codacy-local"
        a["workers"][0]["allowed_paths"]=["playground/safe.py"]
        self.assertEqual(validate(a)["worker_count"],2)
    def test_repair_in_parallel(self):
        a=sample();a["workers"][0]["phase"]="repair"
        with self.assertRaises(ValueError):validate(a)
    def test_no_workers(self):
        a=sample();a["workers"]=[]
        with self.assertRaises(ValueError):validate(a)
    def test_no_scope(self):
        a=sample();del a["workers"][1]["allowed_paths"]
        with self.assertRaises(ValueError):validate(a)

if __name__=="__main__":
    unittest.main()
