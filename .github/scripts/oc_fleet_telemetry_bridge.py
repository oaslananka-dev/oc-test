#!/usr/bin/env python3
from contextlib import contextmanager
from functools import lru_cache
"""Local-only OIDC receiver for schema-checked, content-free OpenCode telemetry."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time

ISSUER = "https://token.actions.githubusercontent.com"
AUDIENCE = "ocfilo-telemetry"
REPO = "oaslananka-dev/oc-test"
WORKFLOW = REPO + "/.github/workflows/oc-fleet-worker.yml@refs/heads/main"
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")

def approved_worker_repos():
    # The receiver owns its own explicit allowlist; task text or unsigned HTTP
    # payloads can NEVER authorize an additional worker repository.
    raw = os.environ.get("FLEET_TELEMETRY_WORKER_REPOS", REPO)
    values = [x.strip() for x in raw.split(",")]
    if (not 1 <= len(values) <= 20
            or any(not REPO_RE.fullmatch(x) or
                   any(part in (".","..") or part.startswith(".")
                       for part in x.split("/")) for x in values)
            or len({x.casefold() for x in values}) != len(values)):
        raise ValueError("Invalid receiver worker repository allowlist")
    return frozenset(values)

KINDS = {"step_start", "step_finish", "tool_use", "error"}
KEYS = {"run_id","attempt","seq","kind","tool","status","tokens_input",
        "tokens_output","tokens_reasoning","cache_read","cache_write","cost_usd"}
TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")
SAFE_TOOLS = {"bash","read","write","edit","glob","grep","list","patch",
              "apply_patch","webfetch","task","mcp","skill","context7","lsp","other"}


@lru_cache(maxsize=1)
def signing_keys():
    import jwt
    return jwt.PyJWKClient(ISSUER + "/.well-known/jwks",
                           cache_keys=True, lifespan=900, timeout=4)


def verified_identity(jwt_token):
    import jwt
    key = signing_keys().get_signing_key_from_jwt(jwt_token)
    c = jwt.decode(jwt_token, key.key, algorithms=["RS256"],
                   audience=AUDIENCE, issuer=ISSUER,
                   options={"require":["exp","iat","iss","aud","sub"]}, leeway=20)
    repo = c.get("repository")
    if (repo not in approved_worker_repos()
            or c.get("workflow_ref") !=
               repo + "/.github/workflows/oc-fleet-worker.yml@refs/heads/main"
            or c.get("ref") != "refs/heads/main"
            or c.get("event_name") != "workflow_dispatch"):
        raise ValueError("untrusted GitHub workflow")
    run,attempt=str(c.get("run_id","")),str(c.get("run_attempt",""))
    if not re.fullmatch(r"[0-9]{1,20}",run) or not re.fullmatch(r"[1-9][0-9]{0,4}",attempt):
        raise ValueError("invalid attested run")
    return repo,run,attempt


def validate_event(item,run,attempt):
    if not isinstance(item,dict) or not set(item)<=KEYS:
        raise ValueError("unrecognised event fields")
    if str(item.get("run_id"))!=run or str(item.get("attempt"))!=attempt:
        raise ValueError("run identity mismatch")
    seq=item.get("seq")
    if type(seq) is not int or not 1<=seq<=10000 or item.get("kind") not in KINDS:
        raise ValueError("invalid sequence/type")
    tool=item.get("tool","")
    state=item.get("status","")
    if not isinstance(tool,str) or (tool and tool not in SAFE_TOOLS):
        raise ValueError("unsafe tool label")
    if not isinstance(state,str) or state not in ("","unknown","running","completed","pending","error"):
        raise ValueError("unsafe status")
    numbers={}
    for k in ("tokens_input","tokens_output","tokens_reasoning",
              "cache_read","cache_write","cost_usd"):
        if k in item:
            v=item[k]
            if type(v) not in (int,float) or not 0<=v<=(100000 if k=="cost_usd" else 10**12):
                raise ValueError("invalid telemetry counter")
            numbers[k]=v
    return (seq,item["kind"],tool,state,json.dumps(numbers,separators=(",",":")))


class Store:
    def __init__(self,path):
        self.path=str(path)
        self.lock=threading.RLock()
        with self.db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS binding
                (run TEXT, attempt TEXT, issue TEXT, created INTEGER,
                 comment_id TEXT, revision INTEGER, updated INTEGER DEFAULT 0,
                 reported_seq INTEGER DEFAULT 0,
                 PRIMARY KEY(run,attempt));
              CREATE TABLE IF NOT EXISTS event
                (run TEXT,attempt TEXT,seq INTEGER,kind TEXT,tool TEXT,
                 status TEXT,metrics TEXT, PRIMARY KEY(run,attempt,seq));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(binding)")}
            if "repo" not in columns:
                db.execute("ALTER TABLE binding ADD COLUMN repo TEXT")
                db.execute("UPDATE binding SET repo=? WHERE repo IS NULL", (REPO,))

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def register(self,run,attempt,issue,repo=REPO):
        if not re.fullmatch(r"[0-9]{1,20}",str(run)) or not re.fullmatch(r"[1-9][0-9]{0,4}",str(attempt)):
            raise ValueError("bad run identity")
        if not re.fullmatch(r"[A-Z][A-Z0-9-]{1,35}",issue):
            raise ValueError("bad Multica issue")
        if repo not in approved_worker_repos():
            raise ValueError("unapproved worker repository")
        with self.lock,self.db() as db:
            old=db.execute("SELECT issue,repo FROM binding WHERE run=? AND attempt=?",
                           (str(run),str(attempt))).fetchone()
            if old and (old[0]!=issue or old[1]!=repo):
                raise ValueError("already bound to another issue or repository")
            db.execute("INSERT OR IGNORE INTO binding(run,attempt,issue,created,repo)"
                       " VALUES(?,?,?,?,?)",(str(run),str(attempt),issue,int(time.time()),repo))
            db.commit()

    def ingest(self,run,attempt,events,repo=REPO):
        if not isinstance(events,list) or not 1<=len(events)<=16:
            raise ValueError("invalid event batch")
        with self.lock,self.db() as db:
            row=db.execute("SELECT created,repo FROM binding WHERE run=? AND attempt=?",
                           (run,attempt)).fetchone()
            if not row or row[1]!=repo or not 0<=int(time.time())-row[0]<=86400:
                raise ValueError("unknown, cross-repository or expired run")
            for ev in events:
                seq,kind,tool,status,nums=validate_event(ev,run,attempt)
                db.execute("INSERT OR IGNORE INTO event VALUES(?,?,?,?,?,?,?)",
                           (run,attempt,seq,kind,tool,status,nums))
            db.commit()

    def summary(self,run,attempt):
        with self.db() as db:
            meta=db.execute("SELECT issue,comment_id,revision,updated,repo FROM binding"
                            " WHERE run=? AND attempt=?",(run,attempt)).fetchone()
            if not meta:return None
            rows=db.execute("SELECT seq,kind,metrics FROM event WHERE run=? AND attempt=?"
                            " ORDER BY seq",(run,attempt)).fetchall()
        metrics={"tokens_input":0,"tokens_output":0,"cost_usd":0}
        for seq,kind,raw in rows:
            if kind=="step_finish":
                data=json.loads(raw)
                for key in metrics:metrics[key]+=data.get(key,0)
        return {"issue":meta[0],"comment":meta[1],"revision":meta[2],
                "updated":meta[3],"repo":meta[4],"events":len(rows),
                "steps":sum(x[1]=="step_finish" for x in rows),
                "tools":sum(x[1]=="tool_use" for x in rows),**metrics}

    def pending(self):
        with self.db() as db:
            return db.execute("SELECT run,attempt FROM binding WHERE updated<?"
                              " AND EXISTS (SELECT 1 FROM event WHERE event.run=binding.run"
                              " AND event.attempt=binding.attempt AND event.seq>binding.reported_seq)",
                              (int(time.time())-15,)).fetchall()

    def set_comment(self,run,attempt,comment_id,revision):
        with self.lock,self.db() as db:
            db.execute("UPDATE binding SET comment_id=?,revision=?,updated=?,"
                       " reported_seq=(SELECT COALESCE(MAX(seq),0) FROM event"
                       " WHERE run=? AND attempt=?)"
                       " WHERE run=? AND attempt=?",
                       (comment_id,revision,int(time.time()),run,attempt,run,attempt))
            db.commit()


def progress_text(run,attempt,item):
    # Deliberately no raw text, @mentions, filenames or arbitrary event properties.
    return ("Çalışma ilerlemesi\n\n"
            f"GitHub run: {run}, deneme: {attempt}\n"
            f"Güvenli olay: {item['events']}, adım: {item['steps']}, araç olayı: {item['tools']}\n"
            f"Input token: {item['tokens_input']}, output token: {item['tokens_output']}\n"
            f"Modelin bildirdiği maliyet (USD): {item['cost_usd']:.5f}\n\n"
            f"https://github.com/{item.get('repo',REPO)}/actions/runs/{run}\n\n"
            "Bu izleme kaydı Multica native execution değildir.")


def publish(store,run,attempt):
    item=store.summary(run,attempt)
    if not item or item["events"]==0:return
    if item["repo"] not in approved_worker_repos():
        raise ValueError("Binding from unapproved repository")
    with tempfile.TemporaryDirectory(prefix="oc-telemetry-") as tmp:
        body=Path(tmp)/"progress.txt"
        body.write_text(progress_text(run,attempt,item),encoding="utf-8")
        if item["comment"]:
            argv=["multica","issue","comment","update",item["comment"],
                  "--expected-revision",str(item["revision"])]
        else:
            argv=["multica","issue","comment","add",item["issue"]]
        argv+=["--content-file",str(body),"--output","json"]
        data=json.loads(subprocess.run(argv,cwd=tmp,capture_output=True,text=True,
                          timeout=20,check=True).stdout)
        if not isinstance(data.get("id"),str) or type(data.get("revision")) is not int:
            raise ValueError("Multica comment receipt incomplete")
        store.set_comment(run,attempt,data["id"],data["revision"])


def validate_multica_issue(issue,repo=None,run=None):
    """Fail closed unless this issue belongs to the configured Multica workspace."""
    workspace = os.environ.get("FLEET_MULTICA_WORKSPACE_ID", "")
    if (not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", workspace)
            or not isinstance(issue, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9]{1,14}-[1-9][0-9]{0,9}",issue)):
        raise ValueError("untrusted Multica issue/workspace")
    result = subprocess.run(["multica","issue","get",issue,"--output","json"],
                            capture_output=True,text=True,check=True,timeout=12)
    data = json.loads(result.stdout)
    if (data.get("identifier") != issue or data.get("workspace_id") != workspace
            or data.get("status") in ("done","cancelled")):
        raise ValueError("issue not active in the approved workspace")
    if repo is not None:
        meta = data.get("metadata") or {}
        if (not isinstance(meta,dict) or meta.get("fleet_worker_repo") != repo
                or str(meta.get("fleet_run_id")) != str(run)
                or meta.get("fleet_result") != "worker_dispatched"
                or data.get("status") != "in_progress"):
            raise ValueError("run not bound to the requested Multica task")
    return True


def make_handler(store,verifier,issue_validator=validate_multica_issue):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!="/health":self.send_error(404);return
            self.send_response(200);self.end_headers();self.wfile.write(b"ok")
        def do_POST(self):
            if self.path not in ("/ingest","/register"):
                self.send_error(404)
                return
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 1<=length<=65536:raise ValueError("bad request size")
                header=self.headers.get("Authorization","")
                if not header.startswith("Bearer "):raise ValueError("missing OIDC")
                repo,run,attempt=verifier(header[7:])
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError("invalid payload")
                if self.path=="/register":
                    if set(data)!={"issue"} or not issue_validator(data["issue"],repo,run):
                        raise ValueError("unapproved Multica issue")
                    store.register(run,attempt,data["issue"],repo)
                else:
                    if set(data)!={"events"}:
                        raise ValueError("unrecognised payload")
                    store.ingest(run,attempt,data["events"],repo)
                self.send_response(200);self.end_headers();self.wfile.write(b'{"ok":true}')
            except Exception:  # includes invalid signed JWT / JWKS fetch errors; no details exposed
                self.send_error(403,"rejected")
        def log_message(self,fmt,*args):
            pass
    return Handler


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default=str(Path.home()/".local/share/ocfilo-telemetry/events.db"))
    sub=p.add_subparsers(dest="mode",required=True)
    reg=sub.add_parser("register")
    reg.add_argument("--run",required=True);reg.add_argument("--attempt",default="1")
    reg.add_argument("--issue",required=True)
    reg.add_argument("--repo",default=REPO)
    srv=sub.add_parser("serve")
    srv.add_argument("--port",type=int,default=18098)
    srv.add_argument("--multica",action="store_true")
    args=p.parse_args()
    os.umask(0o077)
    path=Path(args.db);path.parent.mkdir(parents=True,exist_ok=True)
    store=Store(path)
    if args.mode=="register":
        store.register(args.run,args.attempt,args.issue,args.repo);print("Run registered");return
    if args.multica:
        def report():
            while True:
                time.sleep(20)
                for run,attempt in store.pending():
                    try:publish(store,run,attempt)
                    except (OSError,ValueError,subprocess.SubprocessError):pass
        threading.Thread(target=report,daemon=True).start()
    # Listen ONLY on loopback: TLS ingress must be separately approved and configured.
    server=HTTPServer(("127.0.0.1",args.port),make_handler(store,verified_identity))
    print("Bridge running on loopback port",args.port,flush=True)
    server.serve_forever()


if __name__=="__main__":
    main()
