#!/usr/bin/env python3
"""Emit fixed-field non-content telemetry for failed F2 OpenCode tasks.

Agent log contents are never printed or uploaded. Counters are not root causes.
"""
import argparse
import json
import re
import stat
from pathlib import Path

SAMPLE_LIMIT = 512 * 1024
SIGNALS = {
    "session_processor_lines": re.compile(rb"session\.processor", re.I),
    "session_prompt_lines": re.compile(rb"session\.prompt", re.I),
    "model_stream_lines": re.compile(rb"(?:modelID=big-pickle|\bllm\b.*\bstream\b)", re.I),
    "stream_error_lines": re.compile(rb"(?:stream[ _-]?error|socket connection was closed)", re.I),
    "rate_limit_lines": re.compile(rb"(?:rate[ _-]?limit|(?:http|status|error)[^a-z0-9]{0,4}429\b)", re.I),
    "provider_error_lines": re.compile(rb"(?:provider[ _-]?error|service unavailable|(?:http|status|error)[^a-z0-9]{0,4}50[234]\b)", re.I),
}


def summarize(path):
    summary = {
        "schema_version": 1, "status": "unavailable",
        "log_bytes": 0, "sample_bytes": 0, "truncated": False,
        "signals": {name: 0 for name in SIGNALS},
        "interpretation": "signals_only_not_root_cause",
    }
    try:
        file = Path(path)
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode):
            return summary
        with file.open("rb") as handle:
            total = info.st_size
            if total > SAMPLE_LIMIT:
                handle.seek(total - SAMPLE_LIMIT)
            sample = handle.read(SAMPLE_LIMIT)
    except (OSError, ValueError, TypeError):
        return summary
    summary.update(status="sampled", log_bytes=total, sample_bytes=len(sample),
                   truncated=total > SAMPLE_LIMIT)
    for line in sample.splitlines():
        for name, pattern in SIGNALS.items():
            if pattern.search(line):
                summary["signals"][name] += 1
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    args = parser.parse_args(argv)
    print("F2_AGENT_DIAGNOSTICS=" + json.dumps(summarize(args.log), sort_keys=True))


if __name__ == "__main__":
    main()
