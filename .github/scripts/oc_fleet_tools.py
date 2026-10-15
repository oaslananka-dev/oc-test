#!/usr/bin/env python3
"""Validated, operator-maintained OpenCode tool profiles from Doppler.

Profiles are configuration, not instructions from the target repository.
All installs run on disposable, non-publishing Actions runners.
"""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

IDENT = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
ENV_REF = re.compile(r"\{env:([A-Z][A-Z0-9_]{0,63})\}\Z")
NPM = re.compile(r"(?:@[a-z0-9][a-z0-9-]*/)?[a-z0-9][a-z0-9.-]*@[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?\Z")
PIP = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[0-9]+\.[0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9.-]*)\Z")

def require(value, reason):
    if not value:
        raise ValueError(reason)

def is_ident(value):
    return isinstance(value, str) and bool(IDENT.fullmatch(value))

def clean_package(items, matcher, max_items=8):
    require(isinstance(items,list) and len(items)<=max_items,"invalid package list")
    require(len(set(map(str,items)))==len(items),"duplicate package")
    for item in items:
        require(isinstance(item,str) and len(item)<160 and matcher.fullmatch(item),
                "package must use exact pinned version")
    return items

def validate_url(raw):
    require(isinstance(raw,str) and len(raw)<350,"bad MCP URL")
    url=urlsplit(raw)
    require(url.scheme=="https" and url.hostname and not url.username
            and not url.password and not url.fragment and not url.query
            and not any(c.isspace() for c in raw),"MCP must use clean HTTPS URL")
    host=url.hostname.lower()
    require(host not in ("localhost", "metadata.google.internal")
            and not host.endswith((".local",".internal",".localhost")),
            "local/metadata MCP endpoints forbidden")
    try:
        ip=ipaddress.ip_address(host)
        require(ip.is_global,"MCP IP must be public")
    except ValueError as exc:
        if str(exc)=="MCP IP must be public":
            raise
    return raw

def validate_profile(profile):
    require(isinstance(profile,dict) and set(profile)<=
            {"plugins","mcp","skills","cli","secrets"},"invalid tool profile keys")
    p={"plugins":[],"mcp":{},"skills":[],"cli":{"npm":[],"pipx":[]},"secrets":[]}
    p["plugins"]=clean_package(profile.get("plugins",[]),NPM)
    cli=profile.get("cli",{})
    require(isinstance(cli,dict) and set(cli)<={"npm","pipx"},"invalid CLI section")
    p["cli"]={"npm":clean_package(cli.get("npm",[]),NPM),
              "pipx":clean_package(cli.get("pipx",[]),PIP)}
    secrets=profile.get("secrets",[])
    require(isinstance(secrets,list) and len(secrets)<=10
            and len(set(map(str,secrets)))==len(secrets)
            and all(isinstance(x,str) and ENV_NAME.fullmatch(x)
                    and x not in ("DOPPLER_TOKEN","GH_TOKEN","GITHUB_TOKEN","GITHUB_PAT")
                    for x in secrets),"invalid declared secret names")
    p["secrets"]=secrets
    mcp=profile.get("mcp",{})
    require(isinstance(mcp,dict) and len(mcp)<=8,"too many MCP servers")
    for name,spec in mcp.items():
        require(is_ident(name) and isinstance(spec,dict),"invalid MCP server")
        kind=spec.get("type")
        require(kind in ("remote","local"),"MCP server type must be remote/local")
        common={"type","timeout"}
        if "timeout" in spec:
            require(type(spec["timeout"]) is int and 1000<=spec["timeout"]<=60000,
                    "MCP timeout out of range")
        if kind=="remote":
            require(set(spec)<=(common|{"url","headers","oauth"}),"unapproved remote MCP field")
            validate_url(spec.get("url"))
            require(spec.get("oauth",False) is False,"unattended OAuth not supported")
            headers=spec.get("headers",{})
            require(isinstance(headers,dict) and len(headers)<=8,"bad MCP headers")
            for key,value in headers.items():
                require(isinstance(key,str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,50}",key),
                        "invalid header name")
                m=ENV_REF.fullmatch(value) if isinstance(value,str) else None
                require(m and m.group(1) in secrets,"MCP headers must use Doppler secret refs")
        else:
            require(set(spec)<=(common|{"command","environment"}),"unapproved local MCP field")
            cmd=spec.get("command")
            require(isinstance(cmd,list) and 1<=len(cmd)<=15
                    and sum(len(x) for x in cmd if isinstance(x,str))<1100
                    and all(isinstance(x,str) and x and len(x)<=250
                            and "\n" not in x and "\r" not in x for x in cmd),
                    "local MCP must supply argv list, not shell code")
            environment=spec.get("environment",{})
            require(isinstance(environment,dict) and len(environment)<=10,
                    "bad local MCP environment")
            for key,value in environment.items():
                m=ENV_REF.fullmatch(value) if isinstance(value,str) else None
                require(isinstance(key,str) and ENV_NAME.fullmatch(key)
                        and m and m.group(1) in secrets,"local MCP vars must be declared secret refs")
        p["mcp"][name]=spec
    skills=profile.get("skills",[])
    require(isinstance(skills,list) and len(skills)<=8,"invalid skills list")
    for skill in skills:
        require(isinstance(skill,dict) and set(skill)=={"name","description","content"}
                and is_ident(skill.get("name"))
                and isinstance(skill.get("description"),str)
                and 1<=len(skill["description"])<=200 and "\n" not in skill["description"]
                and isinstance(skill.get("content"),str)
                and 1<=len(skill["content"])<=4000,"invalid skill definition")
        p["skills"].append(skill)
    require(len({s["name"] for s in skills})==len(skills),"duplicate skill")
    return p

def from_catalog(raw, selected):
    require(isinstance(selected,str) and IDENT.fullmatch(selected),"invalid tool profile name")
    require(isinstance(raw,str) and len(raw)<=28000,"tool catalog too large")
    catalog=json.loads(raw)
    require(isinstance(catalog,dict) and set(catalog)=={"version","profiles"}
            and type(catalog["version"]) is int and catalog["version"]==1
            and isinstance(catalog["profiles"],dict) and len(catalog["profiles"])<=25,
            "tool catalog version or shape invalid")
    require(all(is_ident(x) for x in catalog["profiles"]),"bad profile ID")
    require(selected in catalog["profiles"],"unknown tool profile")
    return validate_profile(catalog["profiles"][selected])

def encode(profile):
    return base64.b64encode(json.dumps(profile,separators=(",",":"),ensure_ascii=False)
                            .encode("utf-8")).decode("ascii")

def decode(value):
    require(isinstance(value,str) and len(value)<38000,"invalid encoded tool profile")
    try:
        profile=json.loads(base64.b64decode(value,validate=True))
    except (ValueError,UnicodeError) as exc:
        raise ValueError("invalid encoded tool profile") from exc
    return validate_profile(profile)

def prepare(profile, root):
    root=Path(root)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if profile["skills"]:
        directory=root/"skills"
        for entry in profile["skills"]:
            folder=directory/entry["name"]
            folder.mkdir(mode=0o700,parents=True,exist_ok=True)
            description=json.dumps(entry["description"],ensure_ascii=False)
            (folder/"SKILL.md").write_text(
                f"---\nname: {entry['name']}\ndescription: {description}\n---\n\n{entry['content']}\n",
                encoding="utf-8")
    config={}
    if profile["plugins"]:
        config["plugins"]=profile["plugins"]
    if profile["mcp"]:
        config["mcp"]={"servers":profile["mcp"]}
    if profile["skills"]:
        config["skills"]=[str(root/"skills")]
    (root/"config.json").write_text(json.dumps(config,ensure_ascii=False),encoding="utf-8")
    (root/"secrets.txt").write_text("\n".join(profile["secrets"])+
                                     ("\n" if profile["secrets"] else ""),encoding="ascii")

def cli():
    require(len(sys.argv)==2 and sys.argv[1] in ("select","prepare"),"expected select|prepare")
    if sys.argv[1]=="select":
        name=os.environ.get("FLEET_TOOL_PROFILE","")
        profile=from_catalog(os.environ["FLEET_TOOL_CATALOG"],name) if name else validate_profile({})
        print("tool_profile_b64="+encode(profile))
    else:
        profile=decode(os.environ["FLEET_TOOL_PROFILE_B64"])
        root=Path(os.environ["RUNNER_TEMP"])/"fleet-selected-tools"
        prepare(profile,root)
        # Operator-reviewed packages, only on the disposable read-only worker.
        for package in profile["cli"]["npm"]:
            subprocess.run(["npm","install","--global","--ignore-scripts",package],check=True)
        for package in profile["cli"]["pipx"]:
            subprocess.run(["pipx","install",package],check=True)
        print("Prepared",len(profile["plugins"]),"plugins,",
              len(profile["mcp"]),"MCPs,",len(profile["skills"]),"skills")

if __name__=="__main__":
    try:
        cli()
    except (KeyError,TypeError,ValueError,OSError,subprocess.CalledProcessError) as exc:
        raise SystemExit("tool profile rejected: "+str(exc)[:180])
