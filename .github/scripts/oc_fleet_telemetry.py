#!/usr/bin/env python3
"""OpenCode JSONL -> bounded, content-free telemetry for an OIDC-authenticated bridge.

Never forwards prompts, model text, tool arguments, raw outputs or credentials.
Failure to publish telemetry must not fail an OpenCode coding task.
"""
import json
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_LINE = 128 * 1024
MAX_EVENTS = 10000
MAX_BATCH = 16
TOOL = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,47}\Z")
ALLOWED = {"step_start", "step_finish", "tool_use", "error"}
SAFE_STATUS = {"running", "completed", "error", "pending"}
SAFE_TOOLS = {"bash","read","write","edit","glob","grep","list","patch",
              "apply_patch","webfetch","task","mcp","skill","context7","lsp"}


def number(x, max_value=10**12):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= max_value:
        return None
    return round(x, 6)


def normalise(value):
    """Return ONLY audited primitives. Unrecognised event payloads are ignored."""
    if not isinstance(value, dict) or value.get("type") not in ALLOWED:
        return None
    part = value.get("part")
    if not isinstance(part, dict):
        part = {}
    kind = value["type"]
    item = {"kind": kind}
    if kind == "tool_use":
        tool = part.get("tool", "")
        if not isinstance(tool, str) or tool not in SAFE_TOOLS:
            tool = "other"
        state = part.get("state", {})
        status = state.get("status") if isinstance(state, dict) else None
        item.update(tool=tool, status=status if status in SAFE_STATUS else "unknown")
    if kind == "step_finish":
        tok = part.get("tokens", {})
        if isinstance(tok, dict):
            for key in ("input", "output", "reasoning", "cache"):
                val = tok.get(key)
                if key == "cache" and isinstance(val, dict):
                    for child in ("read", "write"):
                        count = number(val.get(child))
                        if count is not None:
                            item["cache_" + child] = count
                else:
                    count = number(val)
                    if count is not None:
                        item["tokens_" + key] = count
        cost = number(part.get("cost"), 100000)
        if cost is not None:
            item["cost_usd"] = cost
    return item


def oidc_token(audience):
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    token = os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "pipelines.actions.githubusercontent.com" and not parts.hostname.endswith(".actions.githubusercontent.com"):
        raise ValueError("unexpected Actions OIDC issuer endpoint")
    params = parse_qsl(parts.query, keep_blank_values=True)
    params.append(("audience", audience))
    req = Request(urlunsplit((parts.scheme, parts.netloc, parts.path,
                              urlencode(params), parts.fragment)),
                  headers={"Authorization": "Bearer " + token})
    with urlopen(req, timeout=4) as response:
        return json.load(response)["value"]


def push(endpoint, audience, batch):
    token = oidc_token(audience)
    req = Request(endpoint, data=json.dumps({"events": batch},
                     separators=(",", ":")).encode("utf-8"),
                  headers={"Authorization": "Bearer " + token,
                           "Content-Type": "application/json"},
                  method="POST")
    with urlopen(req, timeout=5) as response:
        if response.status != 200:
            raise ValueError("bridge did not acknowledge events")


def forward(stdin=None, sender=None, *, run_id=None, attempt=None,
            endpoint=None, audience="ocfilo-telemetry"):
    stdin = stdin if stdin is not None else sys.stdin.buffer
    sender = sender or push
    run_id = str(run_id or os.environ.get("GITHUB_RUN_ID", ""))
    attempt = str(attempt or os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    endpoint = endpoint if endpoint is not None else os.environ.get("FLEET_TELEMETRY_URL", "")
    if not re.fullmatch(r"[0-9]{1,20}", run_id) or not re.fullmatch(r"[1-9][0-9]{0,4}", attempt):
        raise ValueError("untrusted GitHub run identity")
    if endpoint and (urlsplit(endpoint).scheme != "https"
                     or not urlsplit(endpoint).hostname
                     or urlsplit(endpoint).username
                     or urlsplit(endpoint).password
                     or urlsplit(endpoint).fragment):
        raise ValueError("telemetry endpoint requires HTTPS")
    pending = []
    seq = 0
    last_flush = time.monotonic()
    offline_until = 0.0
    for line in stdin:
        if len(line) > MAX_LINE:
            continue
        try:
            content = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        item = normalise(content)
        if item is None:
            continue
        seq += 1
        if seq > MAX_EVENTS:
            break
        item.update(run_id=run_id, attempt=attempt, seq=seq)
        pending.append(item)
        if (endpoint and time.monotonic() >= offline_until and
            (len(pending) >= MAX_BATCH or item["kind"] in ("tool_use", "step_finish")
                          or time.monotonic() - last_flush > 10)):
            try:
                sender(endpoint, audience, pending)
                pending.clear()
            except (OSError, ValueError, KeyError, TimeoutError):
                # No agent failure if observability is unavailable; bounded in-memory buffer.
                pending[:] = pending[-MAX_BATCH:]
                offline_until = time.monotonic() + 60
            last_flush = time.monotonic()
    offline_until = 0.0
    if endpoint and pending and time.monotonic() >= offline_until:
        try:
            sender(endpoint, audience, pending)
        except (OSError, ValueError, KeyError, TimeoutError):
            pass
    return seq


if __name__ == "__main__":
    try:
        count = forward()
        print("Telemetry processed %d bounded events" % count, file=sys.stderr)
    except (ValueError, KeyError) as exc:
        print("Telemetry unavailable (%s)" % type(exc).__name__, file=sys.stderr)
        # Never break an otherwise successful coding task.
        sys.exit(0)
