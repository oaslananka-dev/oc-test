#!/usr/bin/env python3
"""Validate dispatch inputs and prepare an OpenCode v2 configuration."""
import json
import os
import re
from pathlib import Path

def prepare(e, dest):
    version=e.get("INPUT_OPENCODE_VERSION","")
    model=e.get("INPUT_MODEL","")
    agent=e.get("INPUT_AGENT","")
    if not re.fullmatch(r"2\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?",version): raise ValueError("Invalid OpenCode v2 version")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:#[A-Za-z0-9_.-]+)?",model): raise ValueError("Invalid provider/model")
    if not re.fullmatch(r"[A-Za-z0-9_/-]+",agent): raise ValueError("Invalid agent")
    if e.get("INPUT_RUN_MODE") not in ("config-only","execute"): raise ValueError("Invalid run mode")
    if e.get("INPUT_OUTPUT_FORMAT") not in ("text","json"): raise ValueError("Invalid output format")
    raw=e.get("INPUT_CONFIG_JSON","{}") or "{}"
    if len(raw)>60000: raise ValueError("Configuration is too large")
    overlay=json.loads(raw)
    if not isinstance(overlay,dict): raise ValueError("config_json must be an object")
    defaults={
      "ci-review":{"mode":"primary","description":"Read-only review","system":"Inspect repository without editing files. Do not execute commands or reveal credentials.","permissions":[{"action":"edit","resource":"*","effect":"deny"},{"action":"shell","resource":"*","effect":"deny"}]},
      "ci-implement":{"mode":"primary","description":"Ephemeral code editing","system":"Implement the task and test locally; never push or publish changes.","permissions":[{"action":"edit","resource":"*","effect":"allow"},{"action":"shell","resource":"*","effect":"allow"},{"action":"shell","resource":"git push *","effect":"deny"}]}
    }
    config={"$schema":"https://opencode.ai/config.json","update":"disable","agents":defaults}
    config.update(overlay)
    if "agents" in overlay:
        if not isinstance(overlay["agents"],dict): raise ValueError("agents must be object")
        config["agents"]={**defaults,**overlay["agents"]}
    config["model"]=model.split("#",1)[0]
    config["default_agent"]=agent
    if agent not in ("build","plan") and agent not in config["agents"]: raise ValueError("Undefined agent")
    if "skills" in config and not isinstance(config["skills"],list): raise ValueError("skills must be array")
    if "plugins" in config and not isinstance(config["plugins"],list): raise ValueError("plugins must be array")
    if "mcp" in config and (not isinstance(config["mcp"],dict) or not isinstance(config["mcp"].get("servers",{}),dict)): raise ValueError("mcp.servers must be object")
    dest.write_text(json.dumps(config,ensure_ascii=False,indent=2)+"\n")
    print("Config generated; agent:",agent,"mode:",e["INPUT_RUN_MODE"],"overrides:",",".join(sorted(overlay)))
if __name__=="__main__":
    try: prepare(os.environ,Path("opencode.json"))
    except (ValueError,json.JSONDecodeError) as exc: raise SystemExit(f"Invalid input: {exc}")
