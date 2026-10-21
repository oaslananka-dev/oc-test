#!/usr/bin/env python3
"""Strict bounded parsing for untrusted F2 artifact JSON. No IO or credentials.

Adapted through review from the five-worker sandbox acceptance in PR #118.
F2 remains the sole authority for task identity and evidence semantics; this
module only rejects ambiguous JSON before those validation gates run.
"""
import json
import math

MAX_JSON_CHARS = 16_384

# The reviewed schema-2 worker result.json root fields. Shared by the F2.3
# scheduler and F2.4 integrator: neither may silently trust extra keys.
EVIDENCE_FIELDS = frozenset({
    "schema_version", "repository", "repository_id", "issue_number",
    "model", "identity", "allowed_paths", "task_id", "round",
    "checkout_sha", "idempotency_key", "workflow_run_id", "run_attempt",
    "worker_outcome", "failure_class", "retryable", "agent_exit_code",
    "agent_seconds", "deadline_seconds", "scope_verified", "published", "error",
})


def _pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Nonstandard JSON numeric constant")


def _finite_float(source):
    value = float(source)
    if not math.isfinite(value):
        raise ValueError("Nonfinite JSON number")
    return value


def parse_strict_object(raw, allowed_keys, *, max_chars=MAX_JSON_CHARS):
    """Parse one bounded JSON object with no duplicates at any depth.

    Allowed keys are scoped to the root; business/schema validation remains
    with the caller. Rejects malformed input rather than repairing it.
    """
    if (type(raw) is not str or type(max_chars) is not int
            or not 1 <= max_chars <= MAX_JSON_CHARS or len(raw) > max_chars):
        raise ValueError("Invalid or oversized JSON object")
    if (not isinstance(allowed_keys, (set, frozenset))
            or any(type(key) is not str for key in allowed_keys)):
        raise ValueError("Invalid JSON root-key allowlist")
    try:
        result = json.loads(
            raw, object_pairs_hook=_pairs_unique,
            parse_constant=_reject_constant, parse_float=_finite_float)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON syntax") from exc
    if type(result) is not dict or not set(result) <= allowed_keys:
        raise ValueError("Unexpected JSON object shape or root keys")
    return result
