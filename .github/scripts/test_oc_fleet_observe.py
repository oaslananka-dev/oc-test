#!/usr/bin/env python3
"""Supervisor read adapter: read-only, bounded, content-free acceptance."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oc_fleet_observe import main, observation, read_only, recent
from oc_fleet_telemetry_bridge import Store
from oc_fleet_telemetry import normalise


class ObserverTests(unittest.TestCase):
    def test_live_run_is_readable_without_raw_content_or_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.sqlite"
            store = Store(path)
            store.register("12345", "1", "OAS-12")
            private = "do-not-export secret prompt or shell command"
            clean = normalise({"type": "tool_use", "part": {
                "tool": "bash", "state": {"status": "completed",
                                          "input": {"command": private},
                                          "output": private}}})
            self.assertEqual(clean, {"kind": "tool_use", "tool": "bash",
                                     "status": "completed"})
            store.ingest("12345", "1", [
                {"run_id": "12345", "attempt": "1", "seq": 1,
                 "kind": "step_start"},
                {"run_id": "12345", "attempt": "1", "seq": 2, **clean},
                {"run_id": "12345", "attempt": "1", "seq": 3,
                 "kind": "step_finish", "tokens_input": 100,
                 "tokens_output": 11, "cost_usd": 0.002},
            ])
            # Simulate one Multica comment already published: the next events
            # have not yet been published. No dependency on Multica itself.
            store.set_comment("12345", "1", "comment-123", 1)
            before = path.read_bytes()
            with read_only(path) as db:
                info = observation(db, "12345", "1", 2)
                self.assertEqual(recent(db, 10)[0]["multica_issue"], "OAS-12")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(info["event_count"], 3)
            self.assertEqual(info["last_observed_phase"], "step_finished")
            self.assertEqual(info["recent_safe_events"][0]["sequence"], 2)
            self.assertEqual(info["reported_usage_totals"]["tokens_input"], 100)
            self.assertEqual(info["github_run_state"], "not_checked")
            self.assertTrue(info["multica_comment_exists"])
            self.assertEqual(info["events_not_yet_reported_to_multica"], 0)
            self.assertNotIn(private, json.dumps(info))
            self.assertNotIn("comment-123", json.dumps(info))
            output = StringIO()
            with redirect_stdout(output):
                main(["--db", str(path), "--run", "12345", "--limit", "1"])
            self.assertEqual(json.loads(output.getvalue())["run"]["recent_safe_events"][0]["sequence"], 3)

    def test_zero_events_and_unpublished_progress_are_not_false_live_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "events.sqlite"
            store = Store(p)
            store.register("50", "1", "OAS-9")
            store.ingest("50", "1", [
                {"run_id": "50", "attempt": "1", "seq": 1,
                 "kind": "step_start"}])
            with read_only(p) as db:
                observed = observation(db, "50", "1", 0)
            self.assertEqual(observed["last_observed_phase"], "step_started")
            self.assertEqual(observed["events_not_yet_reported_to_multica"], 1)
            self.assertEqual(observed["recent_safe_events"], [])
            self.assertFalse(observed["multica_comment_exists"])
            self.assertEqual(observed["github_run_state"], "not_checked")

    def test_malformed_run_and_db_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.sqlite"
            with self.assertRaises(OSError), read_only(path):
                pass
            Store(path).register("21", "1", "OAS-21")
            with read_only(path) as db:
                for run, attempt, limit in (("1;DROP TABLE event", "1", 10),
                                             ("21", "../1", 10),
                                             ("21", "1", 100),
                                             ("missing", "1", 10)):
                    with self.subTest(run=run), self.assertRaises(ValueError):
                        observation(db, run, attempt, limit)
                with self.assertRaises(ValueError):
                    observation(db, "31", "1", 1)
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO event VALUES(?,?,?,?,?,?,?)",
                           ("21", "1", 1, "tool_use", "password",
                            "completed", "{}"))
            with read_only(path) as db, self.assertRaises(ValueError):
                observation(db, "21", "1")
            # Read-only query did not delete/correct malicious DB rows.
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM event").fetchone()[0], 1)
                db.execute("UPDATE event SET tool=?,metrics=?",
                           ("bash", '{"run_id":"21","raw":"sensitive"}'))
            with read_only(path) as db, self.assertRaises(ValueError):
                observation(db, "21", "1")

    def test_recent_and_result_schema_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "events.sqlite"
            store = Store(p)
            for i in range(1, 4):
                store.register(str(i), "1", f"OAS-{i}")
            text = StringIO()
            with redirect_stdout(text):
                main(["--db", str(p), "--recent", "2"])
            doc = json.loads(text.getvalue())
            self.assertEqual(doc["schema_version"], 1)
            self.assertEqual(len(doc["runs"]), 2)
            with read_only(p) as db:
                for invalid in (0, 21, True):
                    with self.assertRaises(ValueError):
                        recent(db, invalid)


if __name__ == "__main__":
    unittest.main()
