#!/usr/bin/env python3
"""Fail-closed Codacy v2 JSON quality gate for scoped F1 Python changes.

Codacy analyze returns 0 for no findings, 1 for findings, 2 for execution errors.
Advisory severities are reported, while Error/High/Critical findings block.
"""
import argparse
import json
from pathlib import Path

BLOCK = {"critical", "high", "error"}
ALLOWED = {"critical", "high", "error", "warning", "medium", "minor", "info", "low", "none"}

def evaluate(report_path, exit_code, expected_paths):
    if exit_code not in (0, 1):
        raise ValueError(f"Codacy execution failed: exit {exit_code}")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Codacy report must be an object")
    issues = report.get("issues")
    errors = report.get("errors")
    tool_results = report.get("toolResults")
    if not isinstance(issues, list) or not isinstance(errors, list) or not isinstance(tool_results, list):
        raise ValueError("Incomplete Codacy report schema")
    if errors:
        raise ValueError(f"Codacy reported {len(errors)} execution errors")
    if not expected_paths or not all(
        p.startswith("playground/") and p.endswith(".py") and ".." not in p.split("/")
        for p in expected_paths
    ):
        raise ValueError("Only changed Python files in playground/ are permitted")
    inspected = [x for x in tool_results if isinstance(x, dict) and x.get("toolId") == "Ruff"]
    if len(inspected) != 1 or inspected[0].get("status") != "success" or inspected[0].get("filesAnalyzed", 0) < len(expected_paths):
        raise ValueError("Ruff did not successfully analyze all requested Python files")
    if inspected[0].get("errorCount", 0) != 0:
        raise ValueError("Ruff reported tool execution errors")
    blockers = []
    advisories = []
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("filePath") not in expected_paths:
            raise ValueError("Finding references a file outside the changed set")
        severity = str(issue.get("severity", "")).lower()
        if severity not in ALLOWED:
            raise ValueError(f"Unrecognized Codacy severity: {severity}")
        if severity in BLOCK:
            blockers.append(issue)
        else:
            advisories.append(issue)
    if exit_code == 0 and issues:
        raise ValueError("Codacy exit code contradicts its report")
    return {"scanned_files": len(expected_paths), "issues": len(issues),
            "blocking": len(blockers), "advisory": len(advisories),
            "blockers": blockers[:12]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--exit-code", type=int, required=True)
    p.add_argument("--files-json", required=True)
    p.add_argument("--summary", required=True)
    a = p.parse_args()
    try:
        files = json.loads(Path(a.files_json).read_text(encoding="utf-8"))
        if not isinstance(files, list) or not all(isinstance(x, str) for x in files):
            raise ValueError("Invalid changed files JSON")
        result = evaluate(a.report, a.exit_code, files)
        Path(a.summary).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print("Codacy checked", result["scanned_files"], "Python files:",
              result["blocking"], "blocking and", result["advisory"], "advisory findings")
        if result["blocking"]:
            raise ValueError("Blocking Codacy findings require a new repair run")
    except (ValueError, OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"ocfilo Codacy quality gate: {e}")

if __name__ == "__main__":
    main()
