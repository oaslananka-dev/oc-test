#!/usr/bin/env python3
"""Offline descriptive analysis of independent capacity-10 Actions runs.

The input is a reviewed snapshot of GitHub job metadata, NOT a live GitHub
authentication claim. Job wall times include setup/finalization and should not
be interpreted as exact model latency. Do not extrapolate an observed pass rate
to provider reliability or use this to raise operational concurrency.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import statistics

MAX_INPUT_BYTES = 128_000
TASKS = frozenset(f"f2-cap10-{ch}" for ch in "abcdefghij")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\Z")
RUN_KEYS = frozenset({"run_id", "created_at", "head_sha", "workflow_conclusion",
                      "state_job_conclusion", "artifact_replay", "jobs"})
JOB_KEYS = frozenset({"task_id", "round", "attempt", "state",
                      "started_at", "completed_at"})
ROOT_KEYS = frozenset({"schema_version", "repository", "source", "runs"})


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonstandard(value):
    raise ValueError("Nonstandard JSON constant")


def _read(path):
    p = Path(path)
    if p.is_symlink() or p.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Unsafe or oversized snapshot")
    data = json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                      parse_constant=_nonstandard)
    if type(data) is not dict or set(data) != ROOT_KEYS:
        raise ValueError("Unexpected snapshot fields")
    if data["schema_version"] != 1 or data["repository"] != "oaslananka-dev/oc-test":
        raise ValueError("Unreviewed snapshot schema or repository")
    if not isinstance(data["source"], str) or len(data["source"]) > 300:
        raise ValueError("Missing provenance description")
    if type(data["runs"]) is not list or not 1 <= len(data["runs"]) <= 100:
        raise ValueError("Expected 1..100 distinct capacity-10 runs")
    return data


def _dt(value):
    if not isinstance(value, str) or UTC.fullmatch(value) is None:
        raise ValueError("Expected exact UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _quantile(values, fraction):
    """Nearest-rank quantile, explicitly descriptive for the observed sample."""
    if not values:
        raise ValueError("Empty latency sample")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def one_sided_lower_bound(successes, trials, alpha=0.05):
    """Exact Clopper-Pearson lower bound for independent Bernoulli trials."""
    if type(successes) is not int or type(trials) is not int or not 0 <= successes <= trials:
        raise ValueError("Invalid successes and trials")
    if successes == 0 or trials == 0:
        return 0.0
    if successes == trials:
        return alpha ** (1 / trials)
    lo, hi = 0.0, 1.0
    # Find p for P(Binomial(trials,p) >= successes) = alpha.
    for _ in range(85):
        mid = (lo + hi) / 2
        tail = sum(math.comb(trials, j) * mid**j * (1-mid)**(trials-j)
                   for j in range(successes, trials+1))
        if tail < alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _run(item):
    if type(item) is not dict or set(item) != RUN_KEYS:
        raise ValueError("Unknown run fields")
    rid = item["run_id"]
    if (type(rid) is not int or rid < 1 or type(item["head_sha"]) is not str
            or SHA40.fullmatch(item["head_sha"]) is None):
        raise ValueError("Invalid run ID or SHA")
    if item["workflow_conclusion"] not in ("success", "failure"):
        raise ValueError("Incomplete workflow metadata")
    if item["state_job_conclusion"] not in ("success", "failure"):
        raise ValueError("Incomplete DAG state job")
    if item["artifact_replay"] not in ("verified", "unavailable"):
        raise ValueError("Unknown artifact verification level")
    _dt(item["created_at"])
    jobs = item["jobs"]
    if type(jobs) is not list or not 10 <= len(jobs) <= 20:
        raise ValueError("Invalid number of worker attempts")
    seen = set()
    first = {}
    durations = []
    retries = 0
    failed = 0
    all_by_task = {}
    for job in jobs:
        if type(job) is not dict or set(job) != JOB_KEYS:
            raise ValueError("Unknown job fields")
        tid, rnd, attempt = job["task_id"], job["round"], job["attempt"]
        if (tid not in TASKS or type(rnd) is not int or type(attempt) is not int
                or not 1 <= rnd <= 6 or rnd != attempt or (tid, rnd) in seen):
            raise ValueError("Unknown worker or duplicate/impossible retry")
        seen.add((tid, rnd))
        all_by_task.setdefault(tid, []).append(job)
        if job["state"] not in ("success", "failure"):
            raise ValueError("Incomplete worker job")
        start, end = _dt(job["started_at"]), _dt(job["completed_at"])
        seconds = int((end - start).total_seconds())
        if end < start or not 0 < seconds <= 2400:
            raise ValueError("Invalid job duration")
        durations.append(seconds)
        failed += job["state"] == "failure"
        retries += rnd > 1
        if rnd == 1:
            first[tid] = job
    if set(first) != TASKS:
        raise ValueError("Ten independent first-round task jobs required")
    for tid, attempts in all_by_task.items():
        ordered = sorted(attempts, key=lambda x: x["round"])
        if [x["round"] for x in ordered] != list(range(1,len(ordered)+1)):
            raise ValueError("Missing predecessor attempt")
        if any(j["state"] != "failure" for j in ordered[:-1]):
            raise ValueError("Retry after task already succeeded")
        if len(ordered) > 2:
            raise ValueError("Exceeds reviewed retry budget")
    clean = (item["workflow_conclusion"] == "success"
             and item["state_job_conclusion"] == "success"
             and retries == 0 and failed == 0
             and all(j["state"] == "success" for j in first.values()))
    if item["workflow_conclusion"] == "success" and not clean:
        raise ValueError("Successful workflow with failed/retried workers")
    start_times = [_dt(j["started_at"]) for j in first.values()]
    return {
        "run_id": rid,
        "run_date": _dt(item["created_at"]).date().isoformat(),
        "source_sha": item["head_sha"],
        "workflow_conclusion": item["workflow_conclusion"],
        "state_job_conclusion": item["state_job_conclusion"],
        "artifact_replay": item["artifact_replay"],
        "first_try_clean": clean,
        "first_round_successes": sum(j["state"] == "success" for j in first.values()),
        "failed_attempts": failed,
        "retry_jobs": retries,
        "first_round_start_spread_seconds": int((max(start_times)-min(start_times)).total_seconds()),
        "job_wall_seconds": {
            "min": min(durations), "median": statistics.median(durations),
            "p90_nearest_rank": _quantile(durations, 0.9),
            "max": max(durations),
        },
        "first_round_job_seconds": [
            int((_dt(j["completed_at"])-_dt(j["started_at"])).total_seconds())
            for j in first.values()],
    }


def analyze(data):
    runs = [_run(item) for item in data["runs"]]
    if len({r["run_id"] for r in runs}) != len(runs):
        raise ValueError("Duplicate workflow run")
    total = len(runs)
    clean = sum(r["first_try_clean"] for r in runs)
    all_first = [n for r in runs for n in r["first_round_job_seconds"]]
    for r in runs:
        del r["first_round_job_seconds"]
    n90 = math.ceil(math.log(0.05)/math.log(0.9))
    n95 = math.ceil(math.log(0.05)/math.log(0.95))
    return {
        "schema_version": 1,
        "source": data["source"],
        "metadata_authenticated_by_this_script": False,
        "confidence_bound_requires_iid_comparable_runs": True,
        "overall_workflow_runs": total,
        "first_try_clean_runs": clean,
        "run_level_clean_fraction_observed": clean/total,
        "run_level_one_sided_95pct_lower_bound": round(
            one_sided_lower_bound(clean,total), 4),
        "first_round_task_successes": sum(r["first_round_successes"] for r in runs),
        "first_round_task_attempts": 10*total,
        "failed_attempts": sum(r["failed_attempts"] for r in runs),
        "retry_jobs": sum(r["retry_jobs"] for r in runs),
        "first_round_job_wall_seconds": {
            "min": min(all_first),
            "median": statistics.median(all_first),
            "p90_nearest_rank": _quantile(all_first,0.9),
            "p95_nearest_rank": _quantile(all_first,0.95),
            "max": max(all_first),
        },
        "candidate_gate_not_authorization": {
            "zero_failure_runs_for_one_sided_95pct_lower_bound_at_least_90pct": n90,
            "zero_failure_runs_for_one_sided_95pct_lower_bound_at_least_95pct": n95,
            "distribution": "Across multiple days/time blocks under comparable pinned configuration",
            "each_run": "10 distinct first-round SUCCESS, zero failures/retries, trusted COMPLETE artifacts, GitHub SUCCESS, no paid model",
            "latency_budget": "No observed worker job wall time above 480 seconds within 600-second agent deadline; descriptive engineering margin, not statistical SLA",
            "operational_cap_preserved": 5,
            "new_live_runs_authorized": False,
        },
        "runs": runs,
    }


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--snapshot", required=True)
    args=p.parse_args(argv)
    try:
        print(json.dumps(analyze(_read(args.snapshot)), indent=2, sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        p.exit(2,"F2 capacity statistics refused invalid snapshot\n")


if __name__ == "__main__":
    main()
