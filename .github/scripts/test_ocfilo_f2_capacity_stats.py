"""Regression tests for offline capacity-10 run-level statistics and gates."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

import ocfilo_f2_capacity_stats as stats

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs/ocfilo-f2-capacity-10-runs.json"


class CapacityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.data = stats._read(SNAPSHOT)

    def test_historical_four_runs_and_separate_evidence_levels(self):
        report = stats.analyze(self.data)
        self.assertEqual(report["overall_workflow_runs"], 4)
        self.assertEqual(report["first_try_clean_runs"], 2)
        self.assertEqual(report["first_round_task_attempts"], 40)
        self.assertEqual(report["first_round_task_successes"], 38)
        self.assertEqual(report["failed_attempts"], 3)
        self.assertEqual(report["retry_jobs"], 2)
        self.assertEqual(report["first_round_job_wall_seconds"]["max"], 619)
        self.assertEqual(report["first_round_job_wall_seconds"]["p95_nearest_rank"], 425)
        self.assertEqual(report["run_level_one_sided_95pct_lower_bound"], 0.0976)
        self.assertEqual([r["artifact_replay"] for r in report["runs"]],
                         ["unavailable", "unavailable", "verified", "verified"])
        self.assertFalse(report["metadata_authenticated_by_this_script"])
        self.assertTrue(report["confidence_bound_requires_iid_comparable_runs"])

    def test_exact_one_sided_confidence_gate_math(self):
        self.assertAlmostEqual(stats.one_sided_lower_bound(29, 29),
                               0.05 ** (1/29))
        self.assertGreater(stats.one_sided_lower_bound(29, 29), 0.90)
        self.assertLess(stats.one_sided_lower_bound(28, 28), 0.90)
        self.assertGreater(stats.one_sided_lower_bound(59, 59), 0.95)
        self.assertLess(stats.one_sided_lower_bound(58, 58), 0.95)
        self.assertEqual(stats.one_sided_lower_bound(0, 4), 0)
        self.assertEqual(stats.one_sided_lower_bound(0, 0), 0)
        with self.assertRaises(ValueError):
            stats.one_sided_lower_bound(3, 2)
        policy = stats.analyze(self.data)["candidate_gate_not_authorization"]
        self.assertEqual(policy["zero_failure_runs_for_one_sided_95pct_lower_bound_at_least_90pct"], 29)
        self.assertEqual(policy["zero_failure_runs_for_one_sided_95pct_lower_bound_at_least_95pct"], 59)
        self.assertFalse(policy["new_live_runs_authorized"])
        self.assertEqual(policy["operational_cap_preserved"], 5)

    def test_duplicate_run_missing_worker_wrong_identity_rejected(self):
        for change in ("duplicate", "missing", "forged_task", "same_round", "unreviewed_field"):
            with self.subTest(change=change):
                x=copy.deepcopy(self.data)
                if change=="duplicate":
                    x["runs"].append(copy.deepcopy(x["runs"][0]))
                elif change=="missing":
                    x["runs"][0]["jobs"].pop()
                elif change=="forged_task":
                    x["runs"][0]["jobs"][0]["task_id"]="f2-cap10-z"
                elif change=="same_round":
                    x["runs"][0]["jobs"][0]["round"]=2
                else:
                    x["runs"][0]["jobs"][0]["token"]="must-not-be-accepted"
                with self.assertRaises(ValueError):
                    stats.analyze(x)

    def test_out_of_order_retry_after_success_and_inconsistent_workflow(self):
        for change in ("successful_with_failure","extra_retry","retry_after_success"):
            with self.subTest(change=change):
                x=copy.deepcopy(self.data)
                if change=="successful_with_failure":
                    x["runs"][1]["workflow_conclusion"]="success"
                elif change=="extra_retry":
                    x["runs"][1]["jobs"].append(copy.deepcopy(x["runs"][1]["jobs"][-1]))
                else:
                    # Historical failure's second round pretends it followed a success
                    w=[j for j in x["runs"][1]["jobs"] if j["task_id"]=="f2-cap10-e" and j["round"]==1]
                    self.assertEqual(len(w),1)
                    w[0]["state"]="success"
                with self.assertRaises(ValueError):
                    stats.analyze(x)

    def test_duplicate_keys_unknown_fields_and_nonfinite_refused(self):
        original=SNAPSHOT.read_text(encoding="utf-8")
        payloads=(
            original.replace('"schema_version": 1,','"schema_version": 1, "schema_version": 1,',1),
            original.rstrip()[:-1] + ',"rogue":"value"}',
            original.replace('"schema_version": 1,','"schema_version": NaN,',1),
            "[]",
        )
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/"data.json"
            for payload in payloads:
                with self.subTest(payload=payload[:70]), self.assertRaises(ValueError):
                    file.write_text(payload)
                    stats._read(file)

    def test_oversized_or_symlink_input_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"data.json"
            p.write_bytes(b"x"*(stats.MAX_INPUT_BYTES+1))
            with self.assertRaises(ValueError):
                stats._read(p)
            link=Path(d)/"alias"
            link.symlink_to(SNAPSHOT)
            with self.assertRaises(ValueError):
                stats._read(link)

    def test_invalid_duration_or_timestamp_refused(self):
        for change in ("reversed","future_offset","zero","unexpected_status"):
            with self.subTest(change=change):
                x=copy.deepcopy(self.data)
                j=x["runs"][0]["jobs"][0]
                if change=="reversed":
                    j["completed_at"]="2020-01-01T00:00:00Z"
                elif change=="future_offset":
                    j["started_at"]="2026-10-13T07:05:06+03:00"
                elif change=="zero":
                    j["completed_at"]=j["started_at"]
                else:
                    j["state"]="cancelled"
                with self.assertRaises(ValueError):
                    stats.analyze(x)

    def test_duration_is_worker_job_wall_time_not_agent_seconds(self):
        report=stats.analyze(self.data)
        self.assertEqual(report["first_round_job_wall_seconds"]["median"],61.5)
        self.assertEqual(report["first_round_job_wall_seconds"]["p90_nearest_rank"],263)
        self.assertEqual(report["runs"][-1]["job_wall_seconds"]["max"],412)
        self.assertEqual(report["runs"][0]["first_round_start_spread_seconds"],4)
        self.assertEqual(report["runs"][-1]["first_round_start_spread_seconds"],1)


if __name__=="__main__":
    unittest.main()
