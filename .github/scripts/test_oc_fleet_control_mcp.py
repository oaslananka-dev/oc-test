"""Fail-closed local agent control: no arbitrary repo/run, raw shell, or auto-merge."""
import argparse
import asyncio
import sys
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import oc_fleet_control_mcp as c
import oc_fleet_submit as intake

KEY="OAS-40"
GOOD={
 "identifier":KEY,"status":"todo","project_id":intake.PROJECT,
 "title":"Test","metadata":{
  "fleet_target_repo":"oaslananka/adxl355",
  "fleet_target_issue":"148","fleet_allowed_path":"playground/fleet/new.json",
  "fleet_allowed_paths":"[]","fleet_worker_repo":"MakerParsDev/oc-test",
  "fleet_worker_account":"MakerParsDev","fleet_target_account":"oaslananka",
  "fleet_model":"opencode/big-pickle","fleet_profile":"base",
  "fleet_agent":"build","fleet_ready":True,
  "fleet_auto_merge":False,"fleet_external_pr_only":True,
 }
}

class TransportTests(unittest.TestCase):
 def test_real_stdio_mcp_handshake_lists_operator_tools(self):
  from mcp import ClientSession, StdioServerParameters
  from mcp.client.stdio import stdio_client
  program=Path(__file__).resolve().with_name("oc_fleet_control_mcp.py")

  async def read_tools():
   settings=StdioServerParameters(command=sys.executable,
                                 args=["-B",str(program)])
   async with stdio_client(settings) as (read,write):
    async with ClientSession(read,write) as session:
     accepted=await session.initialize()
     reply=await session.list_tools()
     return accepted.server_info.name, {t.name for t in reply.tools}
  name,tools=asyncio.run(read_tools())
  self.assertEqual(name,"oc-fleet-control")
  self.assertEqual(len(tools),18)
  self.assertIn("fleet_submit",tools)
  self.assertIn("fleet_merge_reviewed_pr",tools)


class Tests(unittest.TestCase):
 def test_sdk_exports_sixteen_bound_tools(self):
  names={t.name for t in c.SERVER._tool_manager.list_tools()}
  self.assertEqual(len(names),18)
  for name in ('fleet_health','fleet_status','fleet_plan','fleet_submit',
               'fleet_open_issues','fleet_create_issue',
               'fleet_run','fleet_logs','fleet_pr','fleet_checks','fleet_pause_queued',
               'fleet_resume_queued','fleet_cancel_queued','fleet_merge_reviewed_pr',
               'fleet_finalize_reviewed_merge','fleet_supersede_reviewed','fleet_telemetry'):
   self.assertIn(name,names)

 def test_bad_project_task_is_rejected(self):
  doc=copy.deepcopy(GOOD)
  doc["project_id"]="1234"
  with patch.object(c.contract,"read_issue",return_value=doc):
   with self.assertRaisesRegex(ValueError,"Not an authorized"):
    c.fleet_status(KEY)

 def test_bad_ids_fail_before_io(self):
  with patch.object(c.contract,"read_issue") as reader:
   for selector in ("", "OAS-0", "OAS-40; rm -rf /","../"):
    with self.assertRaises(ValueError):
     c.fleet_status(selector)
   reader.assert_not_called()

 def test_readonly_list_uses_only_bounded_canonical_inventory(self):
  with patch.object(c,"combined_inventory",return_value=[copy.deepcopy(GOOD)]):
   self.assertEqual(c.fleet_list(10)["total"],1)
   with self.assertRaises(ValueError):c.fleet_list(1000)

 def test_run_bound_to_task_and_expected_worker(self):
  doc=copy.deepcopy(GOOD);doc["metadata"]["fleet_run_id"]="123"
  with patch.object(c.contract,"read_issue",return_value=doc), patch.object(c,"obj",return_value={
      "id":123,"repository":{"full_name":"unexpected/worker"}}):
   with self.assertRaisesRegex(ValueError,"not bound"):
    c.fleet_run(KEY)

 def test_logs_cannot_read_arbitrary_id_and_redacts(self):
  doc=copy.deepcopy(GOOD);doc["metadata"]["fleet_run_id"]="123"
  with patch.object(c.contract,"read_issue",return_value=doc), patch.object(c,"cmd",return_value="normal\nBearer verysecretvalue\nlast line\n") as fake:
   result=c.fleet_logs(KEY,False,10)
   self.assertEqual(result["run_id"],"123")
   self.assertEqual(result["lines"][1],"[line redacted: potential secret]")
   self.assertIn("123",fake.call_args.args[0])
  with self.assertRaises(ValueError):c.fleet_logs(KEY,False,500)

 def test_approve_submit_precondition_no_confirmation_no_write(self):
  with patch.object(c.intake,"submit") as m:
   with self.assertRaisesRegex(ValueError,"confirm_target"):
    c.fleet_submit("oaslananka/adxl355",148,["src/x.py"],"other#148")
   m.assert_not_called()

 def test_merge_requires_explicit_review_and_immutable_head_before_io(self):
  with patch.object(c.contract,"read_issue") as m:
   with self.assertRaisesRegex(ValueError,"reviewed=true"):
    c.fleet_merge_reviewed_pr(KEY,"a"*40,KEY,False)
   with self.assertRaisesRegex(ValueError,"Exact trusted"):
    c.fleet_merge_reviewed_pr(KEY,"bad",KEY,True)
   m.assert_not_called()

 def test_running_task_cannot_be_cancelled_as_queued(self):
  doc=copy.deepcopy(GOOD)
  doc["status"]="in_progress"
  doc["metadata"]["fleet_run_id"]="40110708978"
  with tempfile.TemporaryDirectory() as td:
   with patch.object(c.intake,"LOCK",Path(td)/"queue.lock"),         patch.object(c.contract,"read_issue",return_value=doc),         patch.object(c,"update") as update:
    with self.assertRaisesRegex(ValueError,"already dispatched"):
     c.fleet_cancel_queued(KEY,KEY)
    update.assert_not_called()

 def test_claim_prevents_cancel(self):
  doc=copy.deepcopy(GOOD)
  with tempfile.TemporaryDirectory() as td:
   claim=Path(td)/".local/state/oc-fleet/claims"/(KEY+".claim")
   claim.parent.mkdir(parents=True)
   claim.write_text(KEY)
   with patch.object(c.Path,"home",return_value=Path(td)),         patch.object(c.contract,"read_issue",return_value=doc):
    with self.assertRaisesRegex(ValueError,"claimed"):
     c.fresh_unstarted(KEY)

 def test_cancel_queued_disables_ready_before_cancel_status(self):
  doc=copy.deepcopy(GOOD)
  steps=[]
  def update(key,name,value,kind):
   steps.append(name+"="+value)
   doc["metadata"][name]=(value=="true") if kind=="bool" else value
  def change(key,state):
   steps.append("status="+state);doc["status"]=state
  with tempfile.TemporaryDirectory() as td:
   with patch.object(c.intake,"LOCK",Path(td)/"queue.lock"),         patch.object(c.contract,"read_issue",side_effect=lambda _:copy.deepcopy(doc)),         patch.object(c,"update",side_effect=update),        patch.object(c,"status_change",side_effect=change),        patch.object(c.Path,"home",return_value=Path(td)):
    out=c.fleet_cancel_queued(KEY,KEY)
    self.assertEqual(out["state"],"cancelled")
    self.assertEqual(steps,["fleet_ready=false","status=cancelled"])

 def test_resume_must_be_control_paused(self):
  doc=copy.deepcopy(GOOD);doc["status"]="blocked"
  doc["metadata"]["fleet_ready"]=False
  with tempfile.TemporaryDirectory() as td:
   with patch.object(c.intake,"LOCK",Path(td)/"queue.lock"),        patch.object(c.Path,"home",return_value=Path(td)),        patch.object(c.contract,"read_issue",return_value=doc),        patch.object(c.intake,"preview") as preview:
    with self.assertRaisesRegex(ValueError,"control-paused"):
     c.fleet_resume_queued(KEY,KEY)
    preview.assert_not_called()

 def test_unsafe_supersession_requires_independent_review(self):
  with patch.object(c,"terminal_script") as cli:
   with self.assertRaisesRegex(ValueError,"semantic equivalence"):
    c.fleet_supersede_reviewed(KEY,281,"a"*40,KEY,False,True)
   cli.assert_not_called()

 def test_finalizer_defaults_to_dry_run(self):
  with patch.object(c,"terminal_script",return_value={"state":"waiting"}) as fn:
   val=c.fleet_finalize_reviewed_merge(KEY,"a"*40,KEY)
   self.assertFalse(val["apply"])
   self.assertNotIn("--apply",fn.call_args.args[1])

 def test_issue_creation_requires_allowed_repo_and_exact_confirmation(self):
  with patch.object(c,"cmd") as runner:
   with self.assertRaisesRegex(ValueError,"confirmation"):
    c.fleet_create_issue("oaslananka/adxl355","A bounded new acceptance test",
                         "Detailed acceptance criteria with safe paths and tests",
                         "wrong/repo")
   runner.assert_not_called()

 def test_open_issue_bad_repo_rejected_before_io(self):
  with patch.object(c,"cmd") as runner:
   with self.assertRaises(ValueError):c.fleet_open_issues("../bad",12)
   runner.assert_not_called()

 def test_no_unrestricted_tool_names(self):
  names=[t.name for t in c.SERVER._tool_manager.list_tools()]
  self.assertFalse(any(w in n for n in names for w in
                       ("shell","exec","delete_repo","admin","secret","force")))

if __name__=="__main__":
 unittest.main()
