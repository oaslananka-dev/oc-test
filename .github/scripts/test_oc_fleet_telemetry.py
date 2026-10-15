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

from oc_fleet_telemetry import forward, normalise
from oc_fleet_telemetry_bridge import Store, make_handler, progress_text, validate_event


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
