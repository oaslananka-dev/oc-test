#!/usr/bin/env python3
"""Offline resource planning for a proposed capacity-10 measurement cohort.

Not a bill, quota lookup, OpenCode model call or authorization to dispatch.
All resource projections are descriptive historical scenarios, not guarantees.
"""
import argparse
import json
import math
from pathlib import Path
import stat
import statistics

MAX_INPUT = 65536
ROOT_KEYS = {"schema_version", "repository", "repository_visibility_at_observation",
             "observation_date_utc", "source", "runs"}
RUN_KEYS = {"run_id", "worker_jobs", "total_jobs", "executed_jobs",
            "active_job_seconds_total", "worker_job_seconds_total",
            "roundup_job_minutes_observed", "standard_ubuntu_runner",
            "artifact_zip_bytes", "artifact_zip_count", "artifact_availability"}


def _unique(items):
    d = {}
    for k, v in items:
        if k in d:
            raise ValueError("Duplicate JSON key")
        d[k] = v
    return d


def load(path):
    p = Path(path)
    info = p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT:
        raise ValueError("Unsafe or oversized resource snapshot")
    data = json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    if type(data) is not dict or set(data) != ROOT_KEYS:
        raise ValueError("Invalid resource snapshot fields")
    if (data["schema_version"] != 1 or data["repository"] != "oaslananka-dev/oc-test"
            or data["repository_visibility_at_observation"] != "public"
            or type(data["source"]) is not str or len(data["source"]) > 300
            or type(data["observation_date_utc"]) is not str):
        raise ValueError("Unreviewed snapshot provenance")
    rows = data["runs"]
    if type(rows) is not list or not 1 <= len(rows) <= 100:
        raise ValueError("Expected a finite nonempty list of runs")
    seen = set()
    for r in rows:
        if type(r) is not dict or set(r) != RUN_KEYS:
            raise ValueError("Unrecognized job resource fields")
        rid = r["run_id"]
        if type(rid) is not int or rid <= 0 or rid in seen:
            raise ValueError("Invalid or duplicated Actions run")
        seen.add(rid)
        for k in ("worker_jobs", "total_jobs", "executed_jobs",
                  "active_job_seconds_total", "worker_job_seconds_total",
                  "roundup_job_minutes_observed"):
            if type(r[k]) is not int or r[k] < 0:
                raise ValueError("Noninteger/negative resource measurement")
        if not (10 <= r["worker_jobs"] <= 20
                and r["executed_jobs"] >= r["worker_jobs"]
                and r["total_jobs"] >= r["executed_jobs"]
                and r["active_job_seconds_total"] >= r["worker_job_seconds_total"] > 0
                and r["roundup_job_minutes_observed"] >=
                    math.ceil(r["active_job_seconds_total"]/60)
                and r["standard_ubuntu_runner"] == "ubuntu-24.04"):
            raise ValueError("Unreviewed runner type or inconsistent job totals")
        known = r["artifact_availability"] == "downloadable"
        if r["artifact_availability"] not in ("downloadable", "not_available"):
            raise ValueError("Invalid artifact provenance")
        if known:
            if (type(r["artifact_zip_bytes"]) is not int
                    or r["artifact_zip_bytes"] <= 0
                    or type(r["artifact_zip_count"]) is not int
                    or r["artifact_zip_count"] < 11):
                raise ValueError("Invalid downloadable artifact data")
        elif r["artifact_zip_bytes"] is not None or r["artifact_zip_count"] is not None:
            raise ValueError("Missing artifact size must not be presented as zero")
    return data


def estimate(data, count=29):
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError("Invalid projected run count")
    rows = data["runs"]
    measured = [r["active_job_seconds_total"]/60 for r in rows]
    known_sizes = [r["artifact_zip_bytes"] for r in rows
                   if r["artifact_availability"] == "downloadable"]
    mean_runner_minutes = statistics.mean(measured)
    max_runner_minutes = max(measured)
    observed_max_archive = max(known_sizes) if known_sizes else None
    return {
        "schema_version": 1,
        "historic_run_count": len(rows),
        "public_repository_observed_not_current_billing_check": True,
        "runner_type": "standard ubuntu-24.04",
        "running_minutes_are_parallel_job_time_sum_not_elapsed_time": True,
        "observed_runner_minutes_per_run": [round(n, 2) for n in measured],
        "observed_mean_runner_minutes_per_run": round(mean_runner_minutes, 2),
        "observed_max_runner_minutes_per_run": round(max_runner_minutes, 2),
        "projected_cohort_size": count,
        "projected_mean_case_runner_minutes": round(mean_runner_minutes*count, 1),
        "projected_sample_max_case_runner_minutes": round(max_runner_minutes*count, 1),
        "projected_first_round_worker_sessions": 10*count,
        "projected_max_worker_sessions_at_two_attempts": 20*count,
        "observed_archive_sizes_bytes": known_sizes,
        "projected_archive_bytes_if_each_matches_largest_available":
            observed_max_archive*count if observed_max_archive is not None else None,
        "projected_distinct_utc_days_minimum": 5,
        "workflow_artifact_retention_days": 7,
        "estimated_github_standard_runner_charge_usd": 0,
        "zero_runner_charge_conditional_on": [
            "repository_remains_public",
            "only_standard GitHub-hosted runner is used",
            "GitHub public repository runner pricing remains free",
        ],
        "unknown_costs_or_limits": [
            "actual OpenCode Big Pickle free tier and rate limits at future run date",
            "OpenCode model/provider internal requests per worker session",
            "GitHub artifact shared account storage usage and accrued charges",
            "GitHub runner job queuing/provider latency changes",
        ],
        "historical_snapshot_authenticated_by_script": False,
        "new_live_runs_authorized": False,
        "operational_capacity_promotion_authorized": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True)
    p.add_argument("--planned-runs", type=int, default=29)
    args = p.parse_args(argv)
    try:
        print(json.dumps(estimate(load(args.snapshot), args.planned_runs),
                         indent=2, sort_keys=True))
    except (OSError, ValueError, TypeError, OverflowError):
        p.exit(2, "F2 capacity resource budget refused invalid input\n")


if __name__ == "__main__":
    main()
