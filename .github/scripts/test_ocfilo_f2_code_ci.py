"""Guard the read-only, SHA-bound code-five acceptance workflow."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/ocfilo-f2-code-review-tests.yml'


class CodeReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding='utf-8')

    def test_only_code_scope_triggers_pr_and_push(self):
        text = self.workflow
        self.assertIn('pull_request:', text)
        self.assertIn('push:', text)
        self.assertIn("'playground/f2/dag/test_code_*.py'", text)
        self.assertIn('workflow_dispatch:', text)
        for denied in ('pull_request_target:', 'schedule:', 'repository_dispatch:',
                       'workflow_run:', 'issue_comment:'):
            self.assertNotIn(denied, text)

    def test_read_only_immutable_checkout_and_bounded_worker(self):
        text = self.workflow
        for required in ("contents: read", "runs-on: ubuntu-24.04",
                         "timeout-minutes: 5", "persist-credentials: false",
                         "uses: actions/checkout@v5",
                         '[[ "$F2_SOURCE_SHA" =~ ^[a-f0-9]{40}$ ]]'):
            self.assertIn(required, text)
        for denied in ('secrets.', 'contents: write', 'GH_TOKEN', 'GITHUB_TOKEN',
                       'npm install', 'opencode run', 'git push', 'gh pr merge'):
            self.assertNotIn(denied, text)

    def test_five_files_and_real_unittest_invocation(self):
        text = self.workflow
        self.assertIn("('path', 'digest', 'json', 'run', 'owner')", text)
        self.assertIn("assert found == expected", text)
        self.assertIn("assert not file.stat().st_mode & 0o111", text)
        self.assertIn("python3 -m unittest discover -s playground/f2/dag -p 'test_code_*.py' -v",
                      text)


if __name__ == '__main__':
    unittest.main()
