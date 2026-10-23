from pathlib import Path
import unittest

class AgentWorkflowTests(unittest.TestCase):
    def test_worker_honors_pinned_agent_and_config(self):
        flow=(Path(__file__).parents[1]/"workflows/oc-fleet-worker.yml").read_text()
        self.assertIn("agent: ${{ steps.contract.outputs.agent }}", flow)
        self.assertIn('FLEET_AGENT_ROLE: ${{ needs.validate.outputs.agent }}', flow)
        self.assertIn('"default_agent": m.get("agent", "build")', flow)
        self.assertIn('cfg["agent"]=custom_agents', flow)
        self.assertIn('agent_configuration(m.get("agent", "build"))', flow)
        self.assertEqual(flow.count('--agent "$FLEET_AGENT_ROLE"'), 2)
        self.assertNotIn("--agent build", flow)
        self.assertIn("Selected agent was overridden", flow)
        # Guard real execution: Path must be imported before sys.path.insert.
        self.assertLess(
            flow.index('from pathlib import Path\n          m=json.loads'),
            flow.index('sys.path.insert(0, str(Path('),
        )

    def test_cli_and_detached_receipt_cannot_change_role(self):
        base=Path(__file__).parent
        handoff=(base/"oc_fleet_multica.py").read_text()
        controller=(base/"oc_fleet_issue.py").read_text()
        detached=(base/"oc_fleet_multica_detached.py").read_text()
        self.assertIn('"--agent", selected_agent_role(meta)', handoff)
        self.assertIn('agent=getattr(args, "agent", "build")', controller)
        self.assertIn('manifest.get("agent", "build")==agent', detached)

if __name__ == "__main__":
    unittest.main()
