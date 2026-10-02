import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from niji.planning import extract_plan_steps, load_plan, normalize_plan, save_plan
from niji.tools.stateful import todo_write


class PlanningTests(unittest.TestCase):
    def test_normalize_plan_bounds_text_and_assigns_stable_step_ids(self):
        plan = normalize_plan([{"content": "  Inspect files  ", "status": "in_progress"}])
        self.assertEqual(plan, [{"id": "step-1", "content": "Inspect files",
                                 "status": "in_progress", "activeForm": ""}])
        self.assertEqual(len(normalize_plan([{"content": "x" * 900}])[0]["content"]), 500)

    def test_rejects_malformed_or_ambiguous_active_steps(self):
        with self.assertRaises(ValueError):
            normalize_plan([{"content": "one", "status": "in_progress"},
                            {"content": "two", "status": "in_progress"}])
        with self.assertRaises(ValueError):
            normalize_plan([{"content": "bad status", "status": "unknown"}])
        with self.assertRaises(ValueError):
            normalize_plan([{"content": "x"}] * 61)

    def test_plan_round_trips_atomically_and_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = [{"content": "Inspect the workspace", "status": "in_progress"},
                        {"content": "Run checks", "status": "pending"}]
            saved = save_plan("session-123", original, root=tmp)
            self.assertEqual(load_plan("session-123", root=tmp), saved)
            path = Path(tmp) / "plans" / "session-123.json"
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_rejects_path_traversal_and_ignores_corrupt_or_symlinked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                save_plan("../outside", [], root=tmp)
            folder = Path(tmp) / "plans"
            folder.mkdir()
            target = folder / "target.json"
            target.write_text(json.dumps({"session_id": "session-x", "items": []}))
            (folder / "session-x.json").symlink_to(target)
            self.assertEqual(load_plan("session-x", root=tmp), [])
            (folder / "session-bad.json").write_text("not json")
            self.assertEqual(load_plan("session-bad", root=tmp), [])

    def test_extracts_only_numbered_steps_for_a_plan_preview(self):
        text = """Goal: polish the app\n\nProposed steps:\n1. Audit current behavior\n2. Implement and test the planner\n\nRisks:\n- Keep user changes safe\n\nVerification:\n- Run unit tests"""
        steps = extract_plan_steps(text)
        self.assertEqual([step["content"] for step in steps],
                         ["Audit current behavior", "Implement and test the planner"])
        self.assertTrue(all(step["status"] == "pending" for step in steps))

    def test_numbered_prose_is_not_mistaken_for_a_plan(self):
        self.assertEqual(extract_plan_steps(
            """My notes from the review:
1. This is a quoted item, not a proposed plan
2. Nor is this one"""), [])
        self.assertEqual([step["content"] for step in extract_plan_steps(
            """1. Inspect the project
2. Run tests""")], ["Inspect the project", "Run tests"])

    def test_todo_write_persists_and_notifies_live_plan_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = SimpleNamespace(session_id="session-abc", plan_callback=None,
                                    todos={"items": []})
            notified = []
            agent.plan_callback = lambda plan, label: notified.append((plan, label))
            state = agent.todos
            with patch("niji.planning.SESSION_DIR", Path(tmp)):
                result = todo_write([{"content": "Inspect", "status": "in_progress"}],
                                    "Inspecting files", ctx={"agent": agent, "todos": state})
                self.assertTrue(result.startswith("[ok] plan saved"))
                self.assertEqual(load_plan("session-abc", root=tmp)[0]["content"], "Inspect")
            self.assertEqual(agent.todos["items"][0]["content"], "Inspect")
            self.assertEqual(notified[0][1], "Inspecting files")


if __name__ == "__main__":
    unittest.main()

