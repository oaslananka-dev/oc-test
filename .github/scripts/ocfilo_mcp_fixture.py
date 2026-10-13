#!/usr/bin/env python3
"""Minimal read-only MCP stdio test fixture for ocfilo's approved v2 profile.

No file access, networking, environment reads, subprocess calls or secrets.
Only a constant capability and static test proof are exposed.
"""
import json
import sys

INFO = {"name": "ocfilo-f1-readonly", "version": "1.0.0"}
TOOL = {
    "name": "proof",
    "description": "Return a constant read-only proof that ocfilo's approved MCP profile is connected.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

def reply(method, params):
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": INFO,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        if params.get("name") != "proof" or params.get("arguments") not in (None, {}):
            return {"isError": True, "content": [{"type": "text", "text": "Unknown tool or arguments"}]}
        return {"content": [{"type": "text", "text": "ocfilo F1 read-only MCP proof: connected on Ubuntu"}],
                "isError": False}
    return None

def main():
    for line in sys.stdin:
        if len(line) > 65536:
            continue
        try:
            data = json.loads(line)
            if not isinstance(data, dict) or "method" not in data or "id" not in data:
                continue
            if not isinstance(data["method"], str) or not isinstance(data.get("params", {}), dict):
                raise ValueError("invalid request")
            result = reply(data["method"], data.get("params", {}))
            if result is None:
                response = {"jsonrpc": "2.0", "id": data["id"], "error": {"code": -32601, "message": "Unknown method"}}
            else:
                response = {"jsonrpc": "2.0", "id": data["id"], "result": result}
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
