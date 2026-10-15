#!/usr/bin/env python3
"""One GitHub issue -> no-frontier ephemeral OpenCode worker -> locally verified PR."""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile, time, uuid
from pathlib import Path
from oc_fleet_contract import parse_manifest, scoped_patch

RUNNER="oaslananka-dev/oc-test"
WORKFLOW="oc-fleet-worker.yml"
REPO=re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}\Z")
SHA=re.compile(r"[0-9a-f]{40}\Z")

def require(ok,message):
    if not ok: raise ValueError(message)

def cmd(argv,env,cwd=None,ok=True):
    p=subprocess.run(argv,cwd=cwd,env=env,text=True,encoding="utf-8",
                     errors="replace",capture_output=True)
    if ok and p.returncode: raise ValueError(f"{argv[0]} {argv[1]} failed: {p.stderr[-320:]}")
    return p

def gh(env,path):
    return json.loads(cmd(["gh","api",path],env).stdout)

def identity(account):
    env=os.environ.copy()
    env["GH_PROMPT_DISABLED"]="1"
    token=cmd(["gh","auth","token","--user",account],env).stdout.strip()
    require(bool(token),f"no gh login for {account}")
    env["GH_TOKEN"]=token
    return env

def repo_issue(repo,issue,env):
    require(bool(REPO.fullmatch(repo)) and type(issue)==int and issue>0,"invalid target")
    data=gh(env,f"repos/{repo}")
    require(data.get("full_name","").lower()==repo.lower(),"repo identity mismatch")
    require((data.get("permissions") or {}).get("push") is True,
            "chosen account cannot push to target")
    require(not data.get("archived"),"repository archived")
    require(not data.get("private"),"private target checkout requires FLEET_CHECKOUT_TOKEN; not enabled")
    branch=data.get("default_branch")
    require(isinstance(branch,str) and branch,"missing default branch")
    obj=gh(env,f"repos/{repo}/issues/{issue}")
    require(obj.get("state")=="open" and "pull_request" not in obj,
            "GitHub issue must be open")
    title=obj.get("title","")
    body=obj.get("body") or ""
    prompt=f"Resolve GitHub issue #{issue} in {repo}.\nTitle: {title}\n\n{body}"
    require(10<=len(prompt)<=8000,"issue description outside 10..8000 chars")
    sha=cmd(["gh","api",f"repos/{repo}/git/ref/heads/{branch}",
             "--jq",".object.sha"],env).stdout.strip()
    require(bool(SHA.fullmatch(sha)),"unverified target SHA")
    return branch,sha,prompt

def allow_target(env,repo,preview):
    result=cmd(["gh","variable","get","FLEET_ALLOWED_REPOS","--repo",RUNNER],env,ok=False)
    allow=([s.strip() for s in result.stdout.split(",") if s.strip()]
           if result.returncode==0 else [RUNNER])
    require(all(REPO.fullmatch(v) for v in allow) and len(allow)<100,"bad runner allowlist")
    if repo.lower() not in {x.lower() for x in allow}:
        allow.append(repo)
        if not preview:
            cmd(["gh","variable","set","FLEET_ALLOWED_REPOS","--repo",RUNNER,
                 "--body",",".join(allow)],env)
    return allow

def manifest_for(repo,issue,sha,branch,prompt,args,allow):
    task=f"gh-{issue}-{uuid.uuid4().hex[:12]}"
    m=dict(schema_version=1,task_id=task,source="rules",repository=repo,
           checkout_sha=sha,base_branch=branch,issue_key=f"GH-{issue}",
           prompt=prompt,model=args.model,profile=args.profile,
           allowed_paths=args.path,phase="implement",publish=False)
    parse_manifest(json.dumps(m,ensure_ascii=False),allow)
    return m

def api_post(env,url,body):
    with tempfile.TemporaryDirectory(prefix="fleet-dispatch-") as t:
        p=Path(t)/"body.json"
        p.write_text(json.dumps(body,ensure_ascii=False),encoding="utf-8")
        raw=cmd(["gh","api","-X","POST",url,"--input",str(p)],env).stdout
        return json.loads(raw) if raw.strip() else {}

def dispatch(env,m):
    body=dict(ref="main",return_run_details=True,inputs={
         "manifest_json":json.dumps(m,separators=(",",":"),ensure_ascii=False),
         "run_mode":"execute"})
    result=api_post(env,f"repos/{RUNNER}/actions/workflows/{WORKFLOW}/dispatches",body)
    number=result.get("workflow_run_id")
    require(type(number)==int and number>0,"dispatch accepted but run ID missing: reconcile before retry")
    return number

def await_run(env,number,timeout=1900):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        obj=gh(env,f"repos/{RUNNER}/actions/runs/{number}")
        if obj.get("status")=="completed":
            require(obj.get("conclusion")=="success",
                    f"worker {number} ended {obj.get('conclusion')}")
            return obj
        time.sleep(12)
    raise ValueError(f"worker still active; resume the saved run ID {number}")

def verify_artifact(env,number,m):
    run=gh(env,f"repos/{RUNNER}/actions/runs/{number}")
    wf=gh(env,f"repos/{RUNNER}/actions/workflows/{WORKFLOW}")
    rep=gh(env,f"repos/{RUNNER}")
    require(run.get("id")==number and run.get("workflow_id")==wf.get("id")
            and run.get("repository",{}).get("id")==rep.get("id")
            and run.get("repository",{}).get("full_name")==RUNNER
            and run.get("status")=="completed" and run.get("conclusion")=="success"
            and run.get("event")=="workflow_dispatch" and run.get("head_branch")=="main"
            and run.get("run_attempt")==1,"untrusted worker run")
    jobs=gh(env,f"repos/{RUNNER}/actions/runs/{number}/jobs?per_page=100")
    require(jobs.get("total_count")==2 and
            {x.get("name"):x.get("conclusion") for x in jobs.get("jobs",[])}
            =={"validate":"success","execute":"success"},"worker jobs not all successful")
    arts=gh(env,f"repos/{RUNNER}/actions/runs/{number}/artifacts?per_page=100")
    name=f"fleet-{number}-1-{m['task_id']}"
    matches=[a for a in arts.get("artifacts",[]) if a.get("name")==name]
    require(arts.get("total_count",0)<=100 and len(matches)==1
            and matches[0].get("expired") is False
            and matches[0].get("workflow_run",{}).get("id")==number,
            "artifact identity mismatch")
    with tempfile.TemporaryDirectory(prefix="fleet-evidence-") as t:
        root=Path(t)
        cmd(["gh","run","download",str(number),"--repo",RUNNER,
             "--name",name,"--dir",str(root)],env)
        require(sorted(x.name for x in root.iterdir())==["changes.patch","result.json"],
                "unexpected artifact entries")
        a=root/"changes.patch";b=root/"result.json"
        require(a.is_file() and b.is_file() and not a.is_symlink() and not b.is_symlink()
                and 0<a.stat().st_size<=100000 and b.stat().st_size<=8192,
                "invalid artifact size/type")
        patch=a.read_bytes(); evidence=json.loads(b.read_text(encoding="utf-8"))
    expected={"schema_version":1,"task_id":m["task_id"],"source":"rules",
              "repository":m["repository"],"checkout_sha":m["checkout_sha"],
              "issue_key":m["issue_key"],"run_id":str(number),"run_attempt":"1",
              "outcome":"SUCCEEDED","published":False}
    require(type(evidence)==dict and set(evidence)==set(expected)|{"paths","patch_sha256"},
            "unexpected evidence schema")
    require(all(type(evidence.get(k))==type(v) and evidence.get(k)==v
                for k,v in expected.items()),"evidence differs from dispatch")
    require(hashlib.sha256(patch).hexdigest()==evidence.get("patch_sha256")
            and b"GIT binary patch" not in patch and b"Binary files " not in patch,
            "artifact SHA256/binary gate")
    paths=evidence.get("paths")
    require(isinstance(paths,list) and 1<=len(paths)<=40
            and len(paths)==len(set(paths))
            and all(isinstance(x,str) and any(
                x.startswith(s) if s.endswith("/") else x==s
                for s in m["allowed_paths"]) for x in paths),"scope mismatch")
    return evidence,patch

def publish(env,repo,branch,m,evidence,patch,readonly=False):
    def head():
        return cmd(["gh","api",f"repos/{repo}/git/ref/heads/{branch}",
                    "--jq",".object.sha"],env).stdout.strip()
    require(head()==m["checkout_sha"],"target base changed")
    with tempfile.TemporaryDirectory(prefix="fleet-target-") as t:
        work=Path(t)/"target"
        cmd(["gh","repo","clone",repo,str(work),"--","--quiet"],env)
        cmd(["git","-C",str(work),"config","core.autocrlf","false"],env)
        cmd(["git","-C",str(work),"checkout","--detach",m["checkout_sha"]],env)
        require(cmd(["git","-C",str(work),"rev-parse","HEAD"],env).stdout.strip()
                ==m["checkout_sha"],"target clone SHA mismatch")
        require(not cmd(["git","-C",str(work),"status","--porcelain"],env).stdout.strip(),
                "target checkout dirty")
        p=Path(t)/"diff.patch";p.write_bytes(patch)
        cmd(["git","-C",str(work),"apply","--check",str(p)],env)
        cmd(["git","-C",str(work),"apply",str(p)],env)
        paths,clean=scoped_patch(work,m["allowed_paths"])
        require(sorted(paths)==sorted(evidence["paths"])
                and hashlib.sha256(clean).hexdigest()==evidence["patch_sha256"],
                "trusted patch re-verification failed")
        if readonly:
            print(json.dumps({"status":"VERIFIED_ONLY","paths":paths},ensure_ascii=False))
            return
        require(head()==m["checkout_sha"],"target base moved before publish")
        branch_name=f"fleet/{m['task_id']}"
        existing=cmd(["git","-C",str(work),"ls-remote","--exit-code",
                      "origin",f"refs/heads/{branch_name}"],env,ok=False)
        require(existing.returncode==2,"duplicate task branch or remote error")
        cmd(["git","-C",str(work),"switch","-c",branch_name],env)
        cmd(["git","-C",str(work),"config","user.name","ocfilo-fleet-controller"],env)
        cmd(["git","-C",str(work),"config","user.email",
             "fleet-controller@users.noreply.github.com"],env)
        cmd(["git","-C",str(work),"add","-A"],env)
        cmd(["git","-C",str(work),"commit","-m",f"fix: {m['issue_key']} worker patch"],env)
        cmd(["git","-C",str(work),"-c","credential.helper=",
             "-c","credential.helper=!gh auth git-credential","push",
             "origin",f"HEAD:refs/heads/{branch_name}"],env)
        num=m["issue_key"].split("-")[1]
        description=(f"From issue #{num}; worker: https://github.com/{RUNNER}/actions/runs/{evidence['run_id']}\n"
                     f"Patch SHA256: {evidence['patch_sha256']}\n\nCloses #{num}\n\n"
                     "Read-only OpenCode worker; locally verified GitHub identity published the PR. "
                     "No automatic merge.")
        url=cmd(["gh","pr","create","--repo",repo,"--head",branch_name,"--base",branch,
                 "--title",f"{m['issue_key']}: verified OpenCode task {m['task_id']}",
                 "--body",description],env).stdout.strip()
        require(url.startswith(f"https://github.com/{repo}/pull/"),"unexpected PR URL")
        print(json.dumps({"status":"PR_CREATED","url":url,"run_id":evidence["run_id"],
                          "paths":paths},ensure_ascii=False))

def receipt(repo,issue):
    loc=Path(os.getenv("LOCALAPPDATA",str(Path.home())))/"ocfilo-fleet"
    loc.mkdir(exist_ok=True)
    return loc/(hashlib.sha256(f"{repo.lower()}#{issue}".encode()).hexdigest()[:24]+".json")

def main():
    a=argparse.ArgumentParser()
    a.add_argument("mode",choices=("run","resume"))
    a.add_argument("--repo",required=True)
    a.add_argument("--issue",required=True,type=int)
    a.add_argument("--path",action="append",default=[])
    a.add_argument("--model",default="opencode/big-pickle")
    a.add_argument("--profile",default="base")
    a.add_argument("--worker-account",default="oaslananka")
    a.add_argument("--target-account",default="oaslananka")
    a.add_argument("--dry-run",action="store_true")
    a.add_argument("--verify-only",action="store_true")
    args=a.parse_args()
    worker=identity(args.worker_account)
    target=identity(args.target_account)
    branch,sha,prompt=repo_issue(args.repo,args.issue,target)
    file=receipt(args.repo,args.issue)
    if args.mode=="run":
        require(not file.exists(),f"existing receipt {file}; resume instead of duplicate dispatch")
        allow=allow_target(worker,args.repo,args.dry_run)
        m=manifest_for(args.repo,args.issue,sha,branch,prompt,args,allow)
        if args.dry_run:
            print(json.dumps({"status":"DRY_RUN","repo":args.repo,"issue":args.issue,
                 "sha":sha,"model":args.model,"profile":args.profile,
                 "scope":args.path,"account":args.target_account},indent=2))
            return
        number=dispatch(worker,m)
        file.write_text(json.dumps({"run_id":number,"manifest":m},indent=2),
                        encoding="utf-8")
        print(f"DISPATCHED: {RUNNER} Actions run {number}; receipt {file}",flush=True)
    else:
        require(file.exists(),"no receipt found for resume")
        data=json.loads(file.read_text(encoding="utf-8"))
        m=data["manifest"];number=data["run_id"]
        require(m["repository"].lower()==args.repo.lower()
                and m["issue_key"]==f"GH-{args.issue}" and m["checkout_sha"]==sha,
                "resume receipt stale or not for this issue")
    await_run(worker,number)
    evidence,patch=verify_artifact(worker,number,m)
    publish(target,args.repo,branch,m,evidence,patch,readonly=args.verify_only)

if __name__=="__main__":
    try: main()
    except (ValueError,TypeError,KeyError,subprocess.CalledProcessError,OSError,
            json.JSONDecodeError) as err:
        print("FLEET_BLOCKED: "+str(err)[:480],file=sys.stderr)
        sys.exit(1)
