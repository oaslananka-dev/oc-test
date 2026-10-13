#!/usr/bin/env python3
"""ocfilo F1: validate manifest and protect the privileged publication boundary."""
import json, os, re, sys
from pathlib import Path

FREE = {"opencode/big-pickle","opencode/ling-3.1-flash-free","opencode/nemotron-3-ultra-free"}
NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,48}\Z")
PATH = re.compile(r"playground/[A-Za-z0-9_.\-/]+\Z")

def fail(s):
    raise SystemExit("ocfilo: "+s)

def prepare():
    raw=os.environ.get("OCFILO_MANIFEST","")
    if not raw or len(raw)>16000: fail("missing or oversized manifest")
    try: m=json.loads(raw)
    except ValueError: fail("invalid JSON")
    if not isinstance(m,dict): fail("manifest must be object")
    allowed={"task_id","repository","phase","prompt","model","agent","profile","publish","issue","pr_number","expected_sha","fix_rounds"}
    if set(m)-allowed: fail("unexpected fields "+str(sorted(set(m)-allowed)))
    if m.get("repository")!="oaslananka-dev/oc-test" or not NAME.fullmatch(str(m.get("task_id",""))): fail("repository/task_id not allowed")
    if m.get("phase") not in ("implement","repair"): fail("phase must be implement or repair")
    if m.get("model") not in FREE or m.get("agent")!="build": fail("only free models and build agent permitted")
    if m.get("profile") not in ("base","runner-check"): fail("unsupported tool profile")
    if type(m.get("publish")) is not bool: fail("publish must be bool")
    if not isinstance(m.get("prompt"),str) or not 4 <= len(m["prompt"]) <= 5000: fail("invalid prompt")
    if type(m.get("fix_rounds",1)) is not int or not 1 <= m.get("fix_rounds",1) <= 3: fail("invalid repair budget")
    if m.get("issue") is not None and (type(m["issue"]) is not int or m["issue"]<1): fail("invalid issue")
    if m["phase"]=="repair":
        if type(m.get("pr_number")) is not int or m["pr_number"]<1: fail("missing PR")
        if not re.fullmatch(r"[a-f0-9]{40}",str(m.get("expected_sha",""))): fail("missing expected SHA")
    elif m.get("pr_number") or m.get("expected_sha"): fail("initial work cannot specify a PR")
    config={"$schema":"https://opencode.ai/config.json","model":m["model"],"default_agent":"build"}
    if m["profile"]=="runner-check": config["skills"]=["./.opencode/skills"]
    temp=Path(os.environ["RUNNER_TEMP"])
    (temp/"ocfilo-config.json").write_text(json.dumps(config),encoding="utf8")
    (temp/"ocfilo-task.json").write_text(json.dumps(m),encoding="utf8")
    ref="main" if m["phase"]=="implement" else "ocfilo/f1-"+m["task_id"]
    with open(os.environ["GITHUB_OUTPUT"],"a",encoding="utf8") as out:
        for k,v in {"ref":ref,"publish":str(m["publish"]).lower(),"task_id":m["task_id"],
                    "phase":m["phase"],"fix_rounds":m.get("fix_rounds",1)}.items():
            out.write(f"{k}={v}\n")
    print(f"Validated {m['task_id']} {m['phase']} with {m['model']} / {m['profile']}")

def gate():
    data=Path(os.environ["OCFILO_PATCH"]).read_bytes()
    if len(data)==0 or len(data)>100000: fail("empty or oversized patch")
    if any(x in data for x in [b"GIT binary patch",b"Binary files ",b"120000",b"160000"]):
        fail("binary, symlink or submodule patches prohibited")
    try: lines=data.decode("utf8").splitlines()
    except UnicodeDecodeError: fail("patch must be UTF8")
    files=[]
    for line in lines:
        if line.startswith("diff --git "):
            match=re.fullmatch(r"diff --git a/(.+) b/(.+)",line)
            if not match or match[1]!=match[2]: fail("rename/ambiguous path prohibited")
            p=match[1]
            if not PATH.fullmatch(p) or any(t in (".","..") for t in p.split("/")) or "//" in p:
                fail("only files beneath playground/ allowed")
            files.append(p)
        elif line.startswith(("--- ","+++ ")):
            p=line[4:]
            if p!="/dev/null" and not re.fullmatch(r"[ab]/playground/[A-Za-z0-9_.\-/]+",p):
                fail("file header outside playground/")
        elif line.startswith(("new file mode ","deleted file mode ","old mode ","new mode ")):
            if not line.endswith("100644"): fail("nonregular file mode denied")
    if not files or len(files)>8: fail("must change 1..8 permitted files")
    print("Verified scoped diff:",", ".join(files))

if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in ("prepare","gate"): fail("expected prepare or gate")
    {"prepare":prepare,"gate":gate}[sys.argv[1]]()
