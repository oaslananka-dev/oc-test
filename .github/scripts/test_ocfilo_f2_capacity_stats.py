"""Regression tests for offline capacity-10 run-level statistics and gates."""
import copy
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path

import ocfilo_f2_capacity_stats as stats

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs/ocfilo-f2-capacity-10-runs.json"


class CapacityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.data = stats._read(SNAPSHOT)

    @staticmethod
    def _synthetic_29_clean_cohort():
        # Deliberately synthetic metadata; this proves only local precheck
        # logic, NEVER that an actual live cohort was executed or approved.
        source=json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        clean=copy.deepcopy(source["runs"][-1])
        original_created=stats._dt(clean["created_at"])
        rows=[]
        for i in range(29):
            row=copy.deepcopy(clean)
            row["run_id"]=99900000+i
            new_created=datetime(2026,11,1,tzinfo=timezone.utc)+timedelta(hours=5*i)
            delta=new_created-original_created
            row["created_at"]=new_created.isoformat().replace("+00:00","Z")
            row["artifact_replay"]="verified"
            for job in row["jobs"]:
                for key in ("started_at","completed_at"):
                    job[key]=(stats._dt(job[key])+delta).isoformat().replace("+00:00","Z")
            rows.append(row)
        source["runs"]=rows
        return source

    def test_historical_cohort_fails_closed_with_clear_reasons(self):
        report=stats.analyze(self.data)
        gate=report["prospective_candidate_cohort_precheck_not_authorization"]
        self.assertFalse(gate["metadata_only_gate_pass"])
        self.assertFalse(gate["promote_capacity_to_ten_authorized"])
        self.assertFalse(gate["new_live_runs_authorized"])
        self.assertEqual(report["historical_clean_runs_with_verified_artifacts"],1)
        self.assertEqual(gate["observed_distinct_utc_days"],2)
        self.assertIn("fewer_than_29_runs",gate["rejection_reasons"])
        self.assertIn("nonclean_run_present",gate["rejection_reasons"])
        self.assertIn("independent_artifact_replay_missing",gate["rejection_reasons"])
        self.assertIn("mixed_checkout_sha",gate["rejection_reasons"])
        self.assertIn("starts_less_than_one_hour_apart",gate["rejection_reasons"])
        self.assertIn("job_wall_time_exceeds_proposed_480s_guardrail",gate["rejection_reasons"])
        self.assertIn("original_gh_workflow_and_snapshot_completeness",
                      gate["external_review_still_required"])

    def test_synthetic_passing_metadata_never_authorizes_production(self):
        source=self._synthetic_29_clean_cohort()
        report=stats.analyze(source)
        gate=report["prospective_candidate_cohort_precheck_not_authorization"]
        self.assertTrue(gate["metadata_only_gate_pass"])
        self.assertEqual(gate["rejection_reasons"],[])
        self.assertFalse(gate["promote_capacity_to_ten_authorized"])
        self.assertFalse(gate["new_live_runs_authorized"])
        self.assertFalse(report["metadata_authenticated_by_this_script"])
        self.assertEqual(gate["requirements"]["minimum_clean_runs"],29)
        self.assertGreaterEqual(gate["observed_distinct_utc_days"],5)
        self.assertGreaterEqual(gate["observed_distinct_utc_six_hour_bins"],3)

    def test_gate_fails_on_each_missing_guardrail(self):
        for kind in ("missing", "unverified", "mixed_sha", "close_in_time",
                     "long_tail", "same_day", "failed_workflow"):
            with self.subTest(kind=kind):
                source=self._synthetic_29_clean_cohort()
                rows=source["runs"]
                if kind=="missing":
                    rows.pop()
                elif kind=="unverified":
                    rows[0]["artifact_replay"]="unavailable"
                elif kind=="mixed_sha":
                    rows[0]["head_sha"]="b"*40
                elif kind=="close_in_time":
                    # Original job timestamps remain legal; only dispatch
                    # spacing is violated. Do not fake a worker success.
                    rows[1]["created_at"]=rows[0]["created_at"]
                elif kind=="long_tail":
                    job=rows[0]["jobs"][0]
                    job["completed_at"]=(stats._dt(job["started_at"])+timedelta(seconds=500)).isoformat().replace("+00:00","Z")
                elif kind=="same_day":
                    for i,row in enumerate(rows):
                        row["created_at"]=(datetime(2026,11,1,tzinfo=timezone.utc)+timedelta(minutes=25*i)).isoformat().replace("+00:00","Z")
                else:
                    rows[0]["workflow_conclusion"]="failure"
                    rows[0]["state_job_conclusion"]="failure"
                gate=stats.analyze(source)["prospective_candidate_cohort_precheck_not_authorization"]
                self.assertFalse(gate["metadata_only_gate_pass"])
                self.assertNotEqual(gate["rejection_reasons"],[])

    def test_special_file_is_refused_before_opening(self):
        if not hasattr(os,"mkfifo"):
            self.skipTest("POSIX FIFO not supported")
        with tempfile.TemporaryDirectory() as directory:
            fifo=Path(directory)/"pipe.json"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                stats._read(fifo)

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
