"""Offline F2.3 planning/resource estimator consistency and safety tests."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
import ocfilo_f2_capacity_resources as budget

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs/ocfilo-f2-capacity-10-resources.json"


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.data = budget.load(SNAPSHOT)

    def test_four_runs_and_29_run_projection(self):
        d = budget.estimate(self.data)
        self.assertEqual(d["historic_run_count"], 4)
        self.assertEqual(d["projected_cohort_size"], 29)
        self.assertEqual(d["projected_first_round_worker_sessions"], 290)
        self.assertEqual(d["projected_max_worker_sessions_at_two_attempts"], 580)
        self.assertEqual(d["observed_runner_minutes_per_run"], [10.28, 16.92, 29.12, 19.0])
        self.assertEqual(d["projected_mean_case_runner_minutes"], 546.0)
        self.assertEqual(d["projected_sample_max_case_runner_minutes"], 844.4)
        self.assertEqual(d["projected_archive_bytes_if_each_matches_largest_available"], 430157)
        self.assertEqual(d["observed_archive_sizes_bytes"], [14833, 13669])
        self.assertFalse(d["new_live_runs_authorized"])
        self.assertFalse(d["operational_capacity_promotion_authorized"])
        self.assertTrue(d["zero_runner_charge_conditional_on"])
        self.assertGreaterEqual(len(d["unknown_costs_or_limits"]), 4)

    def test_count_parameter_and_non_authorization(self):
        for n in (1, 3, 10, 29, 59, 100):
            with self.subTest(n=n):
                d = budget.estimate(self.data, n)
                self.assertEqual(d["projected_first_round_worker_sessions"], n*10)
                self.assertFalse(d["new_live_runs_authorized"])
        for n in (0, 101, 2.0, "29", True):
            with self.subTest(n=n), self.assertRaises(ValueError):
                budget.estimate(self.data, n)

    def test_missing_historical_artifacts_not_treated_as_zero(self):
        for row in self.data["runs"][:2]:
            self.assertIsNone(row["artifact_zip_bytes"])
            self.assertIsNone(row["artifact_zip_count"])
            self.assertEqual(row["artifact_availability"], "not_available")
        for key,value in (("artifact_zip_bytes",0),("artifact_zip_count",11)):
            d=copy.deepcopy(self.data)
            d["runs"][0][key]=value
            with tempfile.TemporaryDirectory() as directory:
                p=Path(directory)/"bad.json"
                p.write_text(json.dumps(d))
                with self.assertRaises(ValueError):
                    budget.load(p)

    def test_untrusted_runner_public_status_or_duplicated_id_rejected(self):
        changes = [
            lambda x:x["runs"][0].update(standard_ubuntu_runner="ubuntu-larger"),
            lambda x:x.update(repository_visibility_at_observation="private"),
            lambda x:x["runs"][1].update(run_id=x["runs"][0]["run_id"]),
            lambda x:x["runs"][0].update(active_job_seconds_total=-1),
            lambda x:x["runs"][0].update(executed_jobs=2),
            lambda x:x["runs"][0].update(artifact_availability="downloadable"),
        ]
        for idx,change in enumerate(changes):
            with self.subTest(idx=idx), tempfile.TemporaryDirectory() as directory:
                d=copy.deepcopy(self.data)
                change(d)
                p=Path(directory)/"bad.json"
                p.write_text(json.dumps(d))
                with self.assertRaises(ValueError):
                    budget.load(p)

    def test_duplicate_json_extra_field_nonfinite_symlink_fifo_refused(self):
        original=SNAPSHOT.read_text()
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"bad.json"
            for raw in (original.replace('"schema_version": 1,','"schema_version": 1, "schema_version": 1,',1),
                        original.rstrip()[:-1]+',"rogue":"x"}',
                        original.replace('"schema_version": 1,','"schema_version": NaN,',1)):
                with self.subTest(kind=raw[:40]), self.assertRaises(ValueError):
                    p.write_text(raw)
                    budget.load(p)
            alias=Path(directory)/"alias"
            alias.symlink_to(SNAPSHOT)
            with self.assertRaises(ValueError):
                budget.load(alias)
            if hasattr(os,"mkfifo"):
                fifo=Path(directory)/"fifo"
                os.mkfifo(fifo)
                with self.assertRaises(ValueError):
                    budget.load(fifo)

    def test_storage_unknown_is_explicit_not_forged(self):
        d=copy.deepcopy(self.data)
        for x in d["runs"]:
            x["artifact_zip_bytes"]=None
            x["artifact_zip_count"]=None
            x["artifact_availability"]="not_available"
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"no-archives.json"
            p.write_text(json.dumps(d))
            loaded=budget.load(p)
        report=budget.estimate(loaded)
        self.assertIsNone(report["projected_archive_bytes_if_each_matches_largest_available"])
        self.assertEqual(report["observed_archive_sizes_bytes"],[])


if __name__ == "__main__":
    unittest.main()
