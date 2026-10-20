#!/usr/bin/env python3
"""One GitHub issue -> no-frontier ephemeral OpenCode worker -> locally verified PR."""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path
from oc_fleet_contract import parse_manifest, scoped_patch
from oc_fleet_identity import github_user_identity, verify_commit

RUNNER="oaslananka-dev/oc-test"
WORKFLOW="oc-fleet-worker.yml"
REPO=re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}\Z")
SHA=re.compile(r"[0-9a-f]{40}\Z")

def require(ok,message):
    if not ok: raise ValueError(message)

def resolve_cli(argv, windows=None):
    """Use the actual npm .cmd launcher on Windows; never invoke via shell=True."""
    if windows is None:
        windows = os.name == "nt"
    if windows and argv[0] == "codacy-analysis":
        launcher = shutil.which("codacy-analysis.cmd")
        require(bool(launcher), "Codacy Windows .cmd shim is missing")
        return [launcher, *argv[1:]]
    return argv


def cmd(argv,env,cwd=None,ok=True,deadline=None):
    try:
        p=subprocess.run(resolve_cli(argv),cwd=cwd,env=env,text=True,encoding="utf-8",
                         errors="replace",capture_output=True,timeout=deadline)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"command exceeded time limit: {argv[0]}") from error
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

def repo_issue(repo,issue,env,*,external_pr_only=False):
    require(bool(REPO.fullmatch(repo)) and type(issue)==int and issue>0,"invalid target")
    data=gh(env,f"repos/{repo}")
    require(data.get("full_name","").lower()==repo.lower(),"repo identity mismatch")
    require((data.get("permissions") or {}).get("push") is True,
            "chosen account cannot push to target")
    require(not data.get("archived"),"repository archived")
    if data.get("allow_squash_merge"):
        squash_defaults = (data.get("squash_merge_commit_title"),
                           data.get("squash_merge_commit_message"))
        if external_pr_only:
            require(repo.lower() != RUNNER.lower()
                    and squash_defaults in (
                        ("COMMIT_OR_PR_TITLE", "COMMIT_MESSAGES"),
                        ("PR_TITLE", "BLANK")),
                    "external PR-only mode requires approved target squash defaults")
        else:
            require(squash_defaults == ("PR_TITLE", "BLANK"),
                    "target repository squash defaults must be PR_TITLE/BLANK; "
                    "do not copy previous author/co-author trailers into main")
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
    # Operator CLI credentials may edit the Doppler config; the Actions
    # service token is read-only and never passed to OpenCode.
    doppler_env=os.environ.copy()
    for key in ("GH_TOKEN","GITHUB_TOKEN","DOPPLER_TOKEN"):
        doppler_env.pop(key,None)
    scope=["--project","oc-test","--config","main"]
    result=cmd(["doppler","secrets","get","FLEET_ALLOWED_REPOS","--plain",*scope],
               doppler_env,ok=False)
    require(result.returncode==0,"Doppler repository allowlist unavailable")
    allow=[s.strip() for s in result.stdout.strip().split(",") if s.strip()]
    require(all(REPO.fullmatch(v) for v in allow) and 1<=len(allow)<100,
            "bad Doppler repository allowlist")
    if repo.lower() not in {x.lower() for x in allow}:
        allow.append(repo)
        if not preview:
            cmd(["doppler","secrets","set","FLEET_ALLOWED_REPOS="+",".join(allow),
                 *scope,"--silent"],doppler_env)
    return allow
def require_approved_target(repo):
    """Multica dispatch cannot expand the owner's Doppler repo authorization."""
    env=os.environ.copy()
    for key in ("GH_TOKEN","GITHUB_TOKEN","DOPPLER_TOKEN"):
        env.pop(key,None)
    reply=cmd(["doppler","secrets","get","FLEET_ALLOWED_REPOS","--plain",
               "--project","oc-test","--config","main"],env,ok=False,deadline=30)
    require(reply.returncode == 0,"Target repository allowlist unavailable")
    allowed=[x.strip() for x in reply.stdout.strip().split(",") if x.strip()]
    require(0 < len(allowed) < 100 and all(REPO.fullmatch(x) for x in allowed),
            "Invalid authorized repository list")
    require(repo.lower() in {x.lower() for x in allowed},
            "Multica target is not explicitly approved in Doppler")
    return allowed


def manifest_for(repo,issue,sha,branch,prompt,args,allow):
    task=f"gh-{issue}-{uuid.uuid4().hex[:12]}"
    m=dict(schema_version=1,task_id=task,source="rules",repository=repo,
           checkout_sha=sha,base_branch=branch,issue_key=f"GH-{issue}",
           prompt=prompt,model=args.model,profile=args.profile,
           allowed_paths=args.path,phase="implement",publish=False,
           integrations=getattr(args,"integration",[]),
           tool_profile=getattr(args,"tool_profile",""))
    selected_issue=getattr(args,"multica_issue","")
    if selected_issue:
        m["multica_issue"]=selected_issue
    parse_manifest(json.dumps(m,ensure_ascii=False),allow)
    return m

def api_post(env,url,body):
    with tempfile.TemporaryDirectory(prefix="fleet-dispatch-") as t:
        p=Path(t)/"body.json"
        p.write_text(json.dumps(body,ensure_ascii=False),encoding="utf-8")
        raw=cmd(["gh","api","-X","POST",url,"--input",str(p)],env).stdout
        return json.loads(raw) if raw.strip() else {}

def authorized_worker_repo(env, selected):
    """Resolve a public runner with verified workflow and caller write access."""
    require(isinstance(selected,str) and bool(REPO.fullmatch(selected)),
            "invalid worker repository")
    if selected.lower()!=RUNNER.lower():
        doppler_env=os.environ.copy()
        for key in ("GH_TOKEN","GITHUB_TOKEN","DOPPLER_TOKEN"):
            doppler_env.pop(key,None)
        allowed=cmd(["doppler","secrets","get","FLEET_WORKER_REPOS","--plain",
                     "--project","oc-test","--config","main"],
                    doppler_env,ok=False)
        require(allowed.returncode==0,"Doppler worker repo allowlist unavailable")
        names=[v.strip() for v in allowed.stdout.strip().split(",") if v.strip()]
        require(1<=len(names)<100 and all(REPO.fullmatch(v) for v in names)
                and selected.lower() in {v.lower() for v in names},
                "worker repository not on the trusted allowlist")
    rep=gh(env,f"repos/{selected}")
    require(rep.get("full_name","").lower()==selected.lower()
            and not rep.get("private") and not rep.get("archived")
            and (rep.get("permissions") or {}).get("push") is True,
            "selected account cannot dispatch to a public worker repository")
    workflow=gh(env,f"repos/{selected}/actions/workflows/{WORKFLOW}")
    require(workflow.get("state")=="active"
            and workflow.get("path")==f".github/workflows/{WORKFLOW}"
            and type(workflow.get("id")) is int,
            "worker repository has no active expected workflow")
    return rep["full_name"]


def dispatch(env,m,runner=RUNNER):
    body=dict(ref="main",return_run_details=True,inputs={
         "manifest_json":json.dumps(m,separators=(",",":"),ensure_ascii=False),
         "run_mode":"execute"})
    result=api_post(env,f"repos/{runner}/actions/workflows/{WORKFLOW}/dispatches",body)
    number=result.get("workflow_run_id")
    require(type(number)==int and number>0,"dispatch accepted but run ID missing: reconcile before retry")
    return number

def await_run(env,number,timeout=1900,runner=RUNNER):
    start=time.monotonic()
    while time.monotonic()-start<timeout:
        obj=gh(env,f"repos/{runner}/actions/runs/{number}")
        if obj.get("status")=="completed":
            require(obj.get("conclusion")=="success",
                    f"worker {number} ended {obj.get('conclusion')}")
            return obj
        time.sleep(12)
    raise ValueError(f"worker still active; resume the saved run ID {number}")

def verify_worker_jobs(response):
    """Accept the legacy two-job workflow or the non-executing telemetry probe job.

    The optional probe is SKIPPED during real execute runs. Reject any extra,
    duplicated or unexpectedly successful jobs rather than weakening the gate.
    """
    jobs=response.get("jobs")
    require(isinstance(jobs,list) and type(response.get("total_count")) is int
            and response["total_count"]==len(jobs),"worker job listing incomplete")
    conclusions=[(entry.get("name"),entry.get("conclusion")) for entry in jobs]
    required={("validate","success"),("execute","success")}
    current=required|{("telemetry-probe","skipped")}
    require(len(conclusions)==len(set(conclusions))
            and set(conclusions) in (required,current),
            "worker jobs not all successful or unknown job present")


def verify_artifact(env,number,m,runner=RUNNER):
    run=gh(env,f"repos/{runner}/actions/runs/{number}")
    wf=gh(env,f"repos/{runner}/actions/workflows/{WORKFLOW}")
    rep=gh(env,f"repos/{runner}")
    require(run.get("id")==number and run.get("workflow_id")==wf.get("id")
            and run.get("repository",{}).get("id")==rep.get("id")
            and run.get("repository",{}).get("full_name")==runner
            and run.get("status")=="completed" and run.get("conclusion")=="success"
            and run.get("event")=="workflow_dispatch" and run.get("head_branch")=="main"
            and run.get("run_attempt")==1,"untrusted worker run")
    jobs=gh(env,f"repos/{runner}/actions/runs/{number}/jobs?per_page=100")
    verify_worker_jobs(jobs)
    arts=gh(env,f"repos/{runner}/actions/runs/{number}/artifacts?per_page=100")
    name=f"fleet-{number}-1-{m['task_id']}"
    matches=[a for a in arts.get("artifacts",[]) if a.get("name")==name]
    require(arts.get("total_count",0)<=100 and len(matches)==1
            and matches[0].get("expired") is False
            and matches[0].get("workflow_run",{}).get("id")==number,
            "artifact identity mismatch")
    with tempfile.TemporaryDirectory(prefix="fleet-evidence-") as t:
        root=Path(t)
        cmd(["gh","run","download",str(number),"--repo",runner,
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

def quality_gates(work, changed_paths, integrations, repo):
    """Pre-PR scans using existing operator logins, never shared with the model."""
    requested=set(integrations)
    if not requested.intersection({"codacy","sonarqube"}):
        return
    if not all(p.startswith("tests/") or p.startswith("src/") for p in changed_paths):
        raise ValueError("quality scan only supports source and test paths")
    sanitized=os.environ.copy()
    for key in ("GH_TOKEN","GITHUB_TOKEN","GITHUB_PAT","OPENAI_API_KEY","CONTEXT7_API_KEY","DOPPLER_TOKEN"):
        sanitized.pop(key,None)
    with tempfile.TemporaryDirectory(prefix="fleet-quality-") as temp:
        if "codacy" in requested:
            cfg=str(Path(temp)/"codacy.config.json")
            x=cmd(["codacy-analysis","init","--auto","--config-file",cfg],
                  sanitized,cwd=str(work),ok=False,deadline=60)
            require(x.returncode==0,"Codacy configuration failed; no PR published")
            x=cmd(["codacy-analysis","analyze","--config-file",cfg,"--no-log",
                   "--no-update-notifier","--tool","Ruff","--tool-timeout","90000",
                   "--install-dependencies","--files",*changed_paths,"--output-format","json",
                   "--output",str(Path(temp)/"codacy.json")],
                  sanitized,cwd=str(work),ok=False,deadline=150)
            require(x.returncode==0,"Codacy found issues or scanner failed; no PR published")
            print("QUALITY_CODACY_PASS",flush=True)
        if "sonarqube" in requested:
            require(repo=="oaslananka/nerlex","SonarQube pilot project binding is Nerlex-only")
            require(len(changed_paths)<=12,"SonarQube pilot file limit exceeded")
            args=["sonar","analyze","-p","oaslananka_nerlex","--format","json"]
            for path in changed_paths: args.extend(["--file",path])
            x=cmd(args,sanitized,cwd=str(work),ok=False)
            require(x.returncode==0,"SonarQube analysis failed; no PR published")
            print("QUALITY_SONAR_PASS",flush=True)
def isolated_quality_gates(work, sha, patch_file, paths, integrations, repo, env, temp):
    """Run quality scanners in a disposable linked worktree, never the PR worktree."""
    if not set(integrations).intersection({"codacy", "sonarqube"}):
        return
    scan=Path(temp)/"quality-scan"
    cmd(["git","-C",str(work),"worktree","add","--detach",str(scan),sha],env)
    try:
        cmd(["git","-C",str(scan),"config","core.autocrlf","false"],env)
        cmd(["git","-C",str(scan),"reset","--hard",sha],env)
        cmd(["git","-C",str(scan),"apply","--check",str(patch_file)],env)
        cmd(["git","-C",str(scan),"apply",str(patch_file)],env)
        quality_gates(scan,paths,integrations,repo)
    finally:
        cmd(["git","-C",str(work),"worktree","remove","--force",str(scan)],env,ok=False)
PUBLIC_TYPES = {"fix", "feat", "test", "docs", "refactor", "perf", "ci", "build", "chore"}
INTERNAL_TERMS = re.compile(r"\b(?:oc-test|opencode|multica|fleet|autopilot|worker|agent|pilot)\b",
                            re.IGNORECASE)

def public_change_metadata(manifest, paths):
    """Produce normal repository-facing copy without execution internals."""
    found=re.search(r"(?m)^Title: ([^\r\n]+)",manifest["prompt"])
    original=found.group(1).strip() if found else ""
    title=re.sub(r"\s*\([^()]*\b(?:opencode|oc-test|multica|fleet|worker|agent|pilot)\b[^()]*\)\s*$",
                 "",original,flags=re.IGNORECASE).strip()
    title=re.sub(r"^(?:TEST|PILOT)\s*:\s*","",title,flags=re.IGNORECASE)
    title=re.sub(r"\s+"," ",title).strip(" .")
    if not title or INTERNAL_TERMS.search(title) or len(title)>88:
        filename=Path(paths[0]).stem.replace("_"," ").replace("-"," ")
        title=("test: improve "+filename+" coverage" if paths[0].startswith("tests/")
               else "fix: address issue "+manifest["issue_key"].split("-")[-1])
    prefix=title.partition(":")[0].lower()
    if prefix not in PUBLIC_TYPES or ": " not in title:
        title=("test: " if all(x.startswith("tests/") for x in paths) else "fix: ")+title
    title=title[:100].rstrip()
    issue_number=manifest["issue_key"].split("-")[-1]
    require(issue_number.isdecimal(),"issue identifier not numeric")
    body="\n".join([
      "## Summary", "",
      "- "+title.split(": ",1)[1].rstrip(".")+".", "",
      "## Files changed", "",
      *("- "+path for path in paths), "",
      "## Validation", "",
      "- Review the required checks and test results on this pull request.", "",
      f"Closes #{issue_number}",
    ])
    suffix=re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")[:38].strip("-")
    task=manifest["task_id"].rsplit("-",1)[-1]
    require(re.fullmatch(r"[a-z0-9]{8,12}",task) is not None,"task suffix invalid")
    new_branch=f"change/{issue_number}-{suffix}-{task}"
    return title, body, new_branch
def safe_base_advance(work, old_sha, new_sha, paths, scopes, env):
    """Prove a monotonic target advance left ALL worker-authorized objects alone.

    Only trusted publisher code calls this; untrusted agent text never opts in.
    Use a complete local Git comparison (not paginated API results).
    """
    require(bool(SHA.fullmatch(old_sha)) and bool(SHA.fullmatch(new_sha))
            and old_sha != new_sha, "invalid target SHA advance")
    ancestor=cmd(["git","-C",str(work),"merge-base","--is-ancestor",
                  old_sha,new_sha],env,ok=False)
    require(ancestor.returncode==0,"target base diverged or was rewritten")
    changed=cmd(["git","-C",str(work),"diff","--no-renames","--name-only",
                 "-z",old_sha,new_sha],env)
    # cmd returns text; null-delimited output is complete and preserves whitespace.
    changed_paths=[p for p in changed.stdout.split("\x00") if p]
    require(0<len(changed_paths)<=200 and len(changed_paths)==len(set(changed_paths)),
            "target base advance too broad or invalid")
    for path in [*scopes,*paths]:
        require(type(path) is str and path and not path.startswith("/")
                and "\x00" not in path and ".." not in path.split("/"),
                "unsafe approved scope")
        identifier=path.rstrip("/")
        def oid(sha):
            out=cmd(["git","-C",str(work),"rev-parse","--verify",
                     f"{sha}:{identifier}"],env,ok=False)
            if out.returncode:
                # Absent on both old and new base is acceptable for new files.
                return None
            value=out.stdout.strip()
            require(bool(SHA.fullmatch(value)),"bad scoped Git object")
            return value
        require(oid(old_sha)==oid(new_sha),
                f"target base changed approved scope: {path[:120]}")
    return changed_paths


def publish(env,repo,branch,m,evidence,patch,readonly=False,expected_account=None,allow_safe_base_advance=False):
    def head():
        return cmd(["gh","api",f"repos/{repo}/git/ref/heads/{branch}",
                    "--jq",".object.sha"],env).stdout.strip()
    live_sha=head()
    moved=live_sha!=m["checkout_sha"]
    require(not moved or allow_safe_base_advance,
            "target base changed")
    if moved:
        require(bool(SHA.fullmatch(live_sha)) and expected_account is not None,
                "untrusted new base or actor")
    with tempfile.TemporaryDirectory(prefix="fleet-target-") as t:
        work=Path(t)/"target"
        cmd(["gh","repo","clone",repo,str(work),"--","--quiet"],env)
        cmd(["git","-C",str(work),"config","core.autocrlf","false"],env)
        cmd(["git","-C",str(work),"checkout","--detach",m["checkout_sha"]],env)
        # Fresh temporary clone: normalize checked-out files after changing autocrlf.
        cmd(["git","-C",str(work),"reset","--hard",m["checkout_sha"]],env)
        require(cmd(["git","-C",str(work),"rev-parse","HEAD"],env).stdout.strip()
                ==m["checkout_sha"],"target clone SHA mismatch")
        require(not cmd(["git","-C",str(work),"status","--porcelain"],env).stdout.strip(),
                "target checkout dirty")
        selected_sha=m["checkout_sha"]
        if moved:
            safe_base_advance(work,m["checkout_sha"],live_sha,
                              evidence["paths"],m["allowed_paths"],env)
            cmd(["git","-C",str(work),"checkout","--detach",live_sha],env)
            cmd(["git","-C",str(work),"reset","--hard",live_sha],env)
            require(not cmd(["git","-C",str(work),"status","--porcelain"],env).stdout.strip(),
                    "new base checkout dirty")
            selected_sha=live_sha
        p=Path(t)/"diff.patch";p.write_bytes(patch)
        cmd(["git","-C",str(work),"apply","--check",str(p)],env)
        cmd(["git","-C",str(work),"apply",str(p)],env)
        paths,clean=scoped_patch(work,m["allowed_paths"])
        require(sorted(paths)==sorted(evidence["paths"])
                and hashlib.sha256(clean).hexdigest()==evidence["patch_sha256"],
                "trusted patch re-verification failed")
        isolated_quality_gates(work,selected_sha,p,paths,
                               m.get("integrations",[]),repo,env,t)
        after_paths,after_patch=scoped_patch(work,m["allowed_paths"])
        require(sorted(after_paths)==sorted(paths)
                and hashlib.sha256(after_patch).hexdigest()==evidence["patch_sha256"],
                "post-analysis publisher patch changed")
        if readonly:
            print(json.dumps({"status":"VERIFIED_ONLY","paths":paths},ensure_ascii=False))
            return
        require(head()==selected_sha,"target base moved before publish")
        title,description,branch_name=public_change_metadata(m,paths)
        existing=cmd(["git","-C",str(work),"ls-remote","--exit-code",
                      "origin",f"refs/heads/{branch_name}"],env,ok=False)
        require(existing.returncode==2,"duplicate task branch or remote error")
        cmd(["git","-C",str(work),"switch","-c",branch_name],env)
        actor=json.loads(cmd(["gh","api","user"],env).stdout)
        require(expected_account is None or actor.get("login","").lower()==expected_account.lower(),
                "authenticated GitHub user differs from the selected target account")
        name,email=github_user_identity(actor)
        cmd(["git","-C",str(work),"config","user.name",name],env)
        cmd(["git","-C",str(work),"config","user.email",email],env)
        cmd(["git","-C",str(work),"add","-A"],env)
        cmd(["git","-C",str(work),"commit","-m",title],env)
        verify_commit(work,name,email)
        require(head()==selected_sha,"target base moved just before push")
        cmd(["git","-C",str(work),"-c","credential.helper=",
             "-c","credential.helper=!gh auth git-credential","push",
             "origin",f"HEAD:refs/heads/{branch_name}"],env)
        require(head()==selected_sha,"target base moved during publication")
        url=cmd(["gh","pr","create","--repo",repo,"--head",branch_name,"--base",branch,
                 "--title",title,
                 "--body",description],env).stdout.strip()
        require(url.startswith(f"https://github.com/{repo}/pull/"),"unexpected PR URL")
        print(json.dumps({"status":"PR_CREATED","url":url,"run_id":evidence["run_id"],
                          "paths":paths},ensure_ascii=False))

def receipt(repo,issue):
    loc=Path(os.getenv("LOCALAPPDATA",str(Path.home())))/"ocfilo-fleet"
    loc.mkdir(exist_ok=True)
    return loc/(hashlib.sha256(f"{repo.lower()}#{issue}".encode()).hexdigest()[:24]+".json")

def retry_receipt(worker_env, path, allowed,runner=RUNNER):
    if not path.exists():
        return []
    require(allowed, f"issue already dispatched; use --retry-failed only after a terminal failure: {path}")
    state=json.loads(path.read_text(encoding="utf-8"))
    require(state.get("worker_repo",RUNNER).lower()==runner.lower(),
            "existing issue receipt belongs to a different worker repository")
    number=state.get("run_id")
    require(type(number)==int and number>0,"previous receipt has no native run ID")
    history=state.get("history",[])
    require(type(history)==list and len(history)<2,
            "maximum three total attempts reached")
    last=gh(worker_env,f"repos/{runner}/actions/runs/{number}")
    require(last.get("id")==number and last.get("status")=="completed"
            and last.get("conclusion") in ("failure","cancelled","timed_out"),
            "prior run is not a verified terminal failure")
    old=state.get("manifest") or {}
    require(old.get("task_id") and old.get("repository"),
            "previous task identity missing")
    return history+[{"task_id":old["task_id"],"run_id":number,
                     "outcome":last["conclusion"]}]

def main():
    a=argparse.ArgumentParser()
    a.add_argument("mode",choices=("run","resume"))
    a.add_argument("--repo",required=True)
    a.add_argument("--issue",required=True,type=int)
    a.add_argument("--path",action="append",default=[])
    a.add_argument("--model",default="opencode/big-pickle")
    a.add_argument("--profile",default="base")
    a.add_argument("--integration",action="append",default=[],choices=("context7","codacy","sonarqube"))
    a.add_argument("--tool-profile",default="",help="Named Doppler profile for task MCP/skills/plugins/CLI")
    a.add_argument("--multica-issue",default="",
                   help="Optional approved Multica issue to receive internal progress")
    a.add_argument("--worker-account",default="oaslananka")
    a.add_argument("--worker-repo",default=RUNNER)
    a.add_argument("--target-account",default="oaslananka")
    a.add_argument("--dry-run",action="store_true")
    a.add_argument("--retry-failed",action="store_true")
    a.add_argument("--dispatch-only",action="store_true",
                   help="Persist verified run ID and return without waiting for worker")
    a.add_argument("--verify-only",action="store_true")
    a.add_argument("--require-approved-target",action="store_true",
                   help="Fail closed on Multica target not already in Doppler allowlist")
    a.add_argument("--external-pr-only",action="store_true",
                   help="Explicit external repo PR publication only: accept COMMIT_OR_PR_TITLE/COMMIT_MESSAGES; never auto-merge")
    a.add_argument("--allow-safe-base-advance",action="store_true",
                   help="Opt-in external PR-only resume when Git ancestry and all approved object IDs remain unchanged")
    a.add_argument("--safe-base-advance-policy",action="store_true",
                   help="Persist immutable opt-in at dispatch for subsequent resume")
    args=a.parse_args()
    require(not args.safe_base_advance_policy or
            (args.external_pr_only and args.require_approved_target
             and not args.retry_failed),
            "safe base policy needs external PR-only target authorization")
    require(not args.allow_safe_base_advance or (args.mode=="resume"
            and args.safe_base_advance_policy
            and args.external_pr_only and args.require_approved_target
            and not args.dry_run and not args.retry_failed
            and not args.dispatch_only),
            "safe base advance requires explicitly authorized external PR-only resume")
    require(not args.dispatch_only or (args.mode == "run" and not args.dry_run
            and not args.verify_only and not args.retry_failed),
            "Dispatch-only cannot resume, retry, preview or publish")
    worker=identity(args.worker_account)
    runner=authorized_worker_repo(worker,args.worker_repo)
    target=identity(args.target_account)
    branch,sha,prompt=repo_issue(args.repo,args.issue,target,external_pr_only=args.external_pr_only)
    file=receipt(args.repo,args.issue)
    if args.tool_profile:
        from oc_fleet_tools import from_catalog
        doppler_env=os.environ.copy()
        for key in ("DOPPLER_TOKEN","GH_TOKEN","GITHUB_TOKEN"):
            doppler_env.pop(key,None)
        catalog=cmd(["doppler","secrets","get","FLEET_TOOL_PROFILES","--plain",
                     "--project","oc-test","--config","main"],doppler_env).stdout
        from_catalog(catalog,args.tool_profile)
    if args.mode=="run":
        history=[] if args.dry_run else retry_receipt(worker,file,args.retry_failed,runner=runner)
        if args.require_approved_target:
            require_approved_target(args.repo)
        allow=allow_target(worker,args.repo,args.dry_run)
        m=manifest_for(args.repo,args.issue,sha,branch,prompt,args,allow)
        if args.dry_run:
            print(json.dumps({"status":"DRY_RUN","repo":args.repo,"issue":args.issue,
                 "sha":sha,"model":args.model,"profile":args.profile,
                 "scope":args.path,"integrations":m["integrations"],"tool_profile":m["tool_profile"],"multica_issue":m.get("multica_issue",""),"account":args.target_account,"worker_repo":runner},indent=2))
            return
        number=dispatch(worker,m,runner=runner)
        file.write_text(json.dumps({"run_id":number,"worker_repo":runner,"manifest":m,"history":history},indent=2),
                        encoding="utf-8")
        print(f"DISPATCHED: {runner} Actions run {number}; receipt {file}",flush=True)
        if args.dispatch_only:
            print(json.dumps({"status":"WORKER_DISPATCHED","run_id":str(number),
                              "worker_repo":runner,"target_repo":args.repo,
                              "target_issue":str(args.issue),"checkout_sha":sha},
                             sort_keys=True),flush=True)
            return
    else:
        require(file.exists(),"no receipt found for resume")
        data=json.loads(file.read_text(encoding="utf-8"))
        m=data["manifest"];number=data["run_id"]
        require(data.get("worker_repo",RUNNER).lower()==runner.lower(),
                "resume worker repository does not match existing receipt")
        require(m["repository"].lower()==args.repo.lower()
                and m["issue_key"]==f"GH-{args.issue}"
                and (m["checkout_sha"]==sha or args.allow_safe_base_advance),
                "resume receipt stale or not for this issue")
        if args.allow_safe_base_advance:
            require(m["base_branch"]==branch and m["prompt"]==prompt
                    and m["allowed_paths"]==args.path and m["model"]==args.model
                    and m["profile"]==args.profile
                    and m.get("tool_profile","")==args.tool_profile
                    and m.get("integrations",[])==args.integration,
                    "target issue or immutable worker selection changed")
    await_run(worker,number,runner=runner)
    evidence,patch=verify_artifact(worker,number,m,runner=runner)
    publish(target,args.repo,branch,m,evidence,patch,readonly=args.verify_only,
            expected_account=args.target_account,
            allow_safe_base_advance=args.allow_safe_base_advance)

if __name__=="__main__":
    try: main()
    except (ValueError,TypeError,KeyError,subprocess.CalledProcessError,OSError,
            json.JSONDecodeError) as err:
        print("FLEET_BLOCKED: "+str(err)[:480],file=sys.stderr)
        sys.exit(1)
