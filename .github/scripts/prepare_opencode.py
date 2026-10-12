#!/usr/bin/env python3
"""Validate a free-only, no-credentials OpenCode V2 GitHub Actions dispatch."""
import json
import os
import re
from pathlib import Path

# Listed as zero-price models in the official OpenCode Console model catalogue
# (2026-10-13). Revalidate when pricing or access changes.
FREE_MODELS = frozenset({
    "opencode/big-pickle",
    "opencode/exo-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/ling-3.1-flash-free",
    "opencode/longcat-2.5-preview-free",
    "opencode/mimo-v2.5-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/muse-spark-1.3-contributor-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/space-bunny-free",
    "opencode/step-5-preview-free",
    "opencode/jev-1.13-free",
})
ALLOWED_OVERRIDES = frozenset({
    "agents", "skills", "plugins", "mcp", "permissions", "commands",
    "formatter", "tools",
})

def validate_models(obj, where="config_json"):
    if isinstance(obj, list):
        for n, item in enumerate(obj):
            validate_models(item, f"{where}[{n}]")
    elif isinstance(obj, dict):
        for key, val in obj.items():
            pos = f"{where}.{key}"
            # Prevent configuring hidden paid models through an agent/command.
            if key in ("model", "small_model", "modelID"):
                if not isinstance(val, str) or val not in FREE_MODELS:
                    raise ValueError(f"{pos}: only approved zero-price OpenCode model IDs are allowed")
            validate_models(val, pos)

def prepare(env, destination):
    version = env.get("INPUT_OPENCODE_VERSION", "2.0.27")
    model = env.get("INPUT_MODEL", "opencode/big-pickle")
    agent = env.get("INPUT_AGENT", "build")
    mode = env.get("INPUT_RUN_MODE", "execute")
    fmt = env.get("INPUT_OUTPUT_FORMAT", "default")
    if not re.fullmatch(r"2\.[0-9]+\.[0-9]+", version):
        raise ValueError("OpenCode version must be pinned to a stable v2.x.y release")
    if model not in FREE_MODELS:
        raise ValueError(f"Paid or unknown model is forbidden: {model!r}. Free models: {', '.join(sorted(FREE_MODELS))}")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", agent):
        raise ValueError("Invalid agent name")
    if mode not in ("config-only", "execute"):
        raise ValueError("Invalid run mode")
    if fmt not in ("default", "json"):
        raise ValueError("Invalid output format")
    raw = env.get("INPUT_CONFIG_JSON", "{}") or "{}"
    if len(raw) > 60000:
        raise ValueError("config_json too large")
    overlay = json.loads(raw)
    if not isinstance(overlay, dict):
        raise ValueError("config_json must be a JSON object")
    unknown = set(overlay) - ALLOWED_OVERRIDES
    if unknown:
        raise ValueError(f"Unsupported or unsafe config_json keys: {', '.join(sorted(unknown))}")
    validate_models(overlay)

    if "agents" in overlay and not isinstance(overlay["agents"], dict):
        raise ValueError("agents must be a JSON object")
    if "skills" in overlay and not isinstance(overlay["skills"], list):
        raise ValueError("skills must be an array")
    if "plugins" in overlay and not isinstance(overlay["plugins"], list):
        raise ValueError("plugins must be an array")
    if "mcp" in overlay and (not isinstance(overlay["mcp"], dict)
            or not isinstance(overlay["mcp"].get("servers", {}), dict)):
        raise ValueError("mcp.servers must be a JSON object")
    if agent not in ("build", "plan") and agent not in overlay.get("agents", {}):
        raise ValueError("Custom agent must be supplied in config_json.agents")

    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "default_agent": agent,
        **overlay,
    }
    destination.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print(f"Validated free model: {model}; agent: {agent}; mode: {mode}")
    print("OpenCode Console/Zen availability is external and not guaranteed without login.")

if __name__ == "__main__":
    try:
        prepare(os.environ, Path("opencode.json"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Input validation failed: {exc}")
