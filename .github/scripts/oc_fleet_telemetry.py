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
from urllib.error import HTTPError

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


def register_progress(endpoint, issue, audience="ocfilo-telemetry"):
    """Register one operator-selected Multica issue before worker execution.

    This function runs in the trusted validation job, not inside OpenCode.
    """
    if not isinstance(issue, str) or not re.fullmatch(
            r"[A-Z][A-Z0-9]{1,14}-[1-9][0-9]{0,9}", issue):
        raise ValueError("invalid Multica tracking issue")
    if not isinstance(endpoint, str):
        raise ValueError("missing telemetry endpoint")
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/ingest")):
        raise ValueError("unapproved registration endpoint")
    destination = urlunsplit((parsed.scheme, parsed.netloc,
                              parsed.path[:-len("/ingest")] + "/register", "", ""))
    token = oidc_token(audience)
    payload = json.dumps({"issue": issue}, separators=(",", ":")).encode("ascii")
    request = Request(destination, data=payload,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"},
                      method="POST")
    with urlopen(request, timeout=7) as response:
        if response.status != 200:
            raise ValueError("registration not acknowledged")


def forward(stdin=None, sender=None, *, run_id=None, attempt=None,
            endpoint=None, audience="ocfilo-telemetry", stats=None):
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
    stats = stats if stats is not None else {}
    stats.update(lines=0, accepted=0, sent=0, failures=0, http_status=0)
    pending = []
    seq = 0
    last_flush = time.monotonic()
    offline_until = 0.0
    for line in stdin:
        stats["lines"] += 1
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
        stats["accepted"] += 1
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
                stats["sent"] += 1
            except HTTPError as exc:
                stats["failures"] += 1
                stats["http_status"] = exc.code
                pending[:] = pending[-MAX_BATCH:]
                offline_until = time.monotonic() + 60
            except (OSError, ValueError, KeyError, TimeoutError):
                stats["failures"] += 1
                # No agent failure if observability is unavailable; bounded in-memory buffer.
                pending[:] = pending[-MAX_BATCH:]
                offline_until = time.monotonic() + 60
            last_flush = time.monotonic()
    if endpoint and pending and time.monotonic() >= offline_until:
        try:
            sender(endpoint, audience, pending)
            stats["sent"] += 1
        except HTTPError as exc:
            stats["failures"] += 1
            stats["http_status"] = exc.code
        except (OSError, ValueError, KeyError, TimeoutError):
            stats["failures"] += 1
    return seq


if __name__ == "__main__":
    register_mode = len(sys.argv) > 1
    try:
        if register_mode:
            if len(sys.argv) != 3 or sys.argv[1] != "register":
                raise ValueError("unsupported telemetry command")
            register_progress(os.getenv("FLEET_TELEMETRY_URL", ""), sys.argv[2])
            print("Signed telemetry registration accepted", file=sys.stderr)
        else:
            stats = {}
            count = forward(stats=stats)
            report = ("Telemetry: lines={lines}, accepted={accepted}, "
                      "sent_batches={sent}, failures={failures}, http={http_status}"
                      .format(**stats))
            print(report, file=sys.stderr)
            summary = os.getenv("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as fp:
                    fp.write(report + "\n")
            if os.getenv("FLEET_TELEMETRY_PROBE") == "1" and stats["sent"] == 0:
                sys.exit(2)
            print("Telemetry processed %d bounded events" % count, file=sys.stderr)
    except (ValueError, KeyError, OSError, TimeoutError) as exc:
        print("Telemetry unavailable (%s)" % type(exc).__name__, file=sys.stderr)
        if register_mode or os.getenv("FLEET_TELEMETRY_PROBE") == "1":
            sys.exit(2)
        sys.exit(0)
