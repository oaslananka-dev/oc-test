#!/usr/bin/env python3
"""Offline security and integration checks for the optional telemetry bridge."""
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from oc_fleet_telemetry import forward, normalise, register_progress
from oc_fleet_telemetry_bridge import Store, make_handler, progress_text, validate_event, validate_multica_issue


class TelemetryTest(unittest.TestCase):
    def test_whitelist_never_exports_content(self):
        text = "secret_token=dp.st.private abc@company.test"
        payload = {"type":"tool_use","part":{"tool":"bash","state":{
            "status":"completed","input":{"command":text},"output":text}}}
        event = normalise(payload)
        self.assertEqual(event, {"kind":"tool_use","tool":"bash","status":"completed"})
        self.assertNotIn(text,json.dumps(event))
        self.assertIsNone(normalise({"type":"text","part":{"text":text}}))
        self.assertIsNone(normalise({"type":"reasoning","text":text}))
        self.assertEqual(normalise({"type":"error","properties":{"message":text}}),
                         {"kind":"error"})

    def test_step_usage_only(self):
        event=normalise({"type":"step_finish","part":{"tokens":{"input":123,"output":45,
              "cache":{"read":25,"write":3}},"cost":0.004,"text":"private"}})
        self.assertEqual(event,{"kind":"step_finish","tokens_input":123,
          "tokens_output":45,"cache_read":25,"cache_write":3,"cost_usd":0.004})

    def test_bounded_forward_on_network_failure(self):
        stream=BytesIO(b'{"type":"step_start","part":{"text":"SECRET"}}\n'
                       b'{"type":"tool_use","part":{"tool":"bash","state":{"status":"completed"}}}\n'
                       b'{"type":"text","part":{"text":"do not send"}}\n')
        outgoing=[]
        def mock_sender(url,aud,batch):
            outgoing.extend(batch)
        count=forward(stream,mock_sender,run_id="123",attempt="1",
                      endpoint="https://telemetry.example.org/ingest")
        self.assertEqual(count,2)
        self.assertEqual(len(outgoing),2)
        self.assertNotIn("SECRET",json.dumps(outgoing))
        count=forward(BytesIO(b'{"type":"tool_use","part":{"tool":"bash"}}\n'),
                      lambda *args: (_ for _ in ()).throw(TimeoutError()),
                      run_id="123",attempt="1",endpoint="https://telemetry.example.org/ingest")
        self.assertEqual(count,1)

    def test_safe_probe_diagnostics_are_bounded_counts(self):
        stats={}
        observed=[]
        def sender(url,audience,events):
            observed.extend(events)
        stream=BytesIO(
            b'{"type":"step_start","part":{"text":"SECRET"}}\n'
            b'{"type":"step_finish","part":{"tokens":{"input":2,"output":3}}}\n')
        count=forward(stream,sender,run_id="123",attempt="1",
                      endpoint="https://example.org/ingest",stats=stats)
        self.assertEqual(count,2)
        self.assertEqual(stats["lines"],2)
        self.assertEqual(stats["accepted"],2)
        self.assertEqual(stats["sent"],1)
        self.assertEqual(stats["failures"],0)
        self.assertNotIn("SECRET",json.dumps(observed))

    def test_sqlite_binding_duplicate_and_claim_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/"events.sqlite")
            s.register("123","1","OAS-9")
            e={"run_id":"123","attempt":"1","seq":1,"kind":"tool_use",
               "tool":"bash","status":"completed"}
            s.ingest("123","1",[e,e])
            self.assertEqual(s.summary("123","1")["events"],1)
            with self.assertRaisesRegex(ValueError,"another"):
                s.register("123","1","OAS-10")
            with self.assertRaises(ValueError):
                s.ingest("999","1",[e])
            with self.assertRaises(ValueError):
                s.ingest("123","1",[{**e,"run_id":"999"}])
            with self.assertRaises(ValueError):
                s.ingest("123","1",[{**e,"raw_message":"secret"}])

    def test_oidc_registration_uses_separate_trusted_endpoint(self):
        requests=[]
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): return False
        def fake_urlopen(req,timeout):
            requests.append(req)
            return Response()
        with patch("oc_fleet_telemetry.oidc_token",return_value="signed-test-token"), patch(
                "oc_fleet_telemetry.urlopen",side_effect=fake_urlopen):
            register_progress("https://bridge.example.org/internal/ingest","OAS-11")
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0].full_url,
                         "https://bridge.example.org/internal/register")
        self.assertEqual(json.loads(requests[0].data),{"issue":"OAS-11"})
        self.assertEqual(requests[0].get_header("Authorization"),
                         "Bearer signed-test-token")
        with patch("oc_fleet_telemetry.oidc_token",side_effect=AssertionError(
                "OIDC should not be requested for invalid input")):
            for url,issue in (
                ("http://bridge.example.org/ingest","OAS-11"),
                ("https://bridge.example.org/other","OAS-11"),
                ("https://bridge.example.org/ingest","OAS-0"),
                ("https://bridge.example.org/ingest","oas-11")):
                with self.subTest(url=url,issue=issue),self.assertRaises(ValueError):
                    register_progress(url,issue)

    def test_http_signed_registration_and_strict_run_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/"events.sqlite")
            def verifier(token):
                if token != "valid":
                    raise ValueError("signed token required")
                return "123","1"
            server=ThreadingHTTPServer(("127.0.0.1",0),
                make_handler(s,verifier,
                             issue_validator=lambda issue: issue=="OAS-11"))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                url="http://127.0.0.1:%d/register" % server.server_port
                def send(issue,token="valid",payload=None):
                    body=json.dumps(payload if payload is not None else {"issue":issue}).encode()
                    return urlopen(Request(url,data=body,headers={
                        "Authorization":"Bearer "+token,
                        "Content-Type":"application/json"}),timeout=3)
                self.assertEqual(send("OAS-11").status,200)
                self.assertEqual(send("OAS-11").status,200)
                self.assertEqual(s.summary("123","1")["issue"],"OAS-11")
                for issue, token, body in (
                    ("OAS-12","valid",None),
                    ("OAS-11","invalid",None),
                    ("OAS-11","valid",{"issue":"OAS-11","other":"unsafe"}),
                ):
                    with self.subTest(issue=issue,token=token):
                        with self.assertRaises(HTTPError) as ctx:
                            send(issue,token,body)
                        self.assertEqual(ctx.exception.code,403)
                event={"run_id":"123","attempt":"1","seq":1,"kind":"step_start"}
                s.ingest("123","1",[event])
                self.assertEqual(s.summary("123","1")["events"],1)
            finally:
                server.shutdown()
                server.server_close()

    def test_workspace_identity_required_for_multica_registration(self):
        from unittest.mock import MagicMock
        import os
        allowed="dacd00a3-eeb9-4f80-9c9e-773062880f87"
        data={"identifier":"OAS-11","workspace_id":allowed,"status":"todo"}
        def fake_run(*args,**kwargs):
            return MagicMock(stdout=json.dumps(data))
        with patch.dict(os.environ,{"FLEET_MULTICA_WORKSPACE_ID":allowed}), patch(
                "oc_fleet_telemetry_bridge.subprocess.run",side_effect=fake_run):
            self.assertTrue(validate_multica_issue("OAS-11"))
            data["status"]="done"
            with self.assertRaises(ValueError):
                validate_multica_issue("OAS-11")
            data["status"]="todo"
            data["workspace_id"]="00000000-0000-0000-0000-000000000000"
            with self.assertRaises(ValueError):
                validate_multica_issue("OAS-11")
        with patch.dict(os.environ,{"FLEET_MULTICA_WORKSPACE_ID":""}):
            with self.assertRaises(ValueError):
                validate_multica_issue("OAS-11")

    def test_agent_observer_path_resolves_from_target_checkout(self):
        workflow = Path(__file__).resolve().parent.parent / "workflows" / "oc-fleet-worker.yml"
        script = workflow.read_text(encoding="utf-8")
        self.assertIn('python3 "$GITHUB_WORKSPACE/runner/.github/scripts/oc_fleet_telemetry.py"', script)
        self.assertNotIn('python3 runner/.github/scripts/oc_fleet_telemetry.py', script)
        self.assertIn('stage_status=("${PIPESTATUS[@]}")', script)
        self.assertIn('rc=${stage_status[0]}', script)
        self.assertIn('${stage_status[1]}', script)
    def test_receiver_rejects_missing_or_wrong_oidc(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/"events.sqlite")
            s.register("123","1","OAS-9")
            server=ThreadingHTTPServer(("127.0.0.1",0),
                      make_handler(s,lambda tok: ("123","1") if tok=="valid" else ("999","1")))
            runner=threading.Thread(target=server.serve_forever,daemon=True)
            runner.start()
            try:
                url="http://127.0.0.1:%s/ingest" % server.server_port
                batch=json.dumps({"events":[{"run_id":"123","attempt":"1",
                       "seq":1,"kind":"step_start"}]}).encode()
                for token in ("invalid",""):
                    headers={"Content-Type":"application/json"}
                    if token:headers["Authorization"]="Bearer "+token
                    with self.assertRaises(HTTPError) as cm:
                        urlopen(Request(url,data=batch,headers=headers),timeout=3)
                    self.assertEqual(cm.exception.code,403)
                req=Request(url,data=batch,headers={"Authorization":"Bearer valid",
                                                       "Content-Type":"application/json"})
                self.assertEqual(urlopen(req,timeout=3).status,200)
                self.assertEqual(s.summary("123","1")["events"],1)
            finally:
                server.shutdown()
                server.server_close()

    def test_public_summary_has_no_hidden_data(self):
        sample={"events":2,"steps":1,"tools":1,"tokens_input":25,
                "tokens_output":7,"cost_usd":0.001}
        text=progress_text("123","1",sample)
        self.assertIn("Multica native execution değildir",text)
        self.assertNotIn("@",text)
        self.assertNotIn("prompt",text.lower())


if __name__=="__main__":
    unittest.main()
