import sys
import types
import unittest
from unittest.mock import patch

from niji.compaction import estimate_tokens, maybe_compact


class TooLargeError(Exception):
    status_code = 413


class CompactionTests(unittest.TestCase):
    def make_agent(self):
        if "niji.agent" not in sys.modules and "openai" not in sys.modules:
            fake_openai = types.ModuleType("openai")
            fake_openai.OpenAI = lambda **kwargs: types.SimpleNamespace(options=kwargs)
            sys.modules["openai"] = fake_openai
        from niji.agent import Agent
        with patch("niji.agent.OpenAI", return_value=object()):
            return Agent({"provider": "groq", "model": "m", "api_key": "k",
                          "base_url": "https://example.test/v1"}, verbose=False)

    def test_forced_compaction_keeps_current_request_even_for_short_transcript(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "environment"},
            {"role": "assistant", "content": "old output " * 5000},
            {"role": "user", "content": "please continue the current fix"},
        ]
        compacted, changed = maybe_compact(messages, None, "m", max_tokens=8000,
                                            keep_recent=6, force=True, summarize=False)
        self.assertTrue(changed)
        self.assertLess(estimate_tokens(compacted), estimate_tokens(messages))
        self.assertEqual(compacted[-1]["content"], "please continue the current fix")
        self.assertIn("Untrusted summary", compacted[1]["content"])

    def test_compaction_starts_at_user_boundary_not_orphan_tool_result(self):
        tool_call = {"id": "call-1", "type": "function",
                     "function": {"name": "grep", "arguments": "{}"}}
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "environment"},
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "", "tool_calls": [tool_call]},
            {"role": "tool", "tool_call_id": "call-1", "content": "old result " * 1000},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current task"},
        ]
        compacted, changed = maybe_compact(messages, None, "m", max_tokens=100,
                                            keep_recent=4, force=True, summarize=False)
        self.assertTrue(changed)
        roles_after_summary = [m["role"] for m in compacted[2:]]
        self.assertNotIn("tool", roles_after_summary)
        self.assertEqual(compacted[-1]["content"], "current task")

    def test_http_413_compacts_once_and_retries_with_smaller_context(self):
        agent = self.make_agent()
        agent.messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "environment"},
            {"role": "assistant", "content": "old output " * 5000},
            {"role": "user", "content": "current user request"},
        ]
        requests = []

        def create(**kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                raise TooLargeError("request too large")
            return []

        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
        msg, text, tools = agent._chat()
        self.assertEqual(len(requests), 2)
        self.assertGreater(estimate_tokens(requests[0]["messages"]),
                           estimate_tokens(requests[1]["messages"]))
        self.assertEqual(requests[1]["messages"][-1]["content"], "current user request")
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(text, "")
        self.assertEqual(tools, [])

    def test_plan_only_sends_no_tools_and_does_not_mutate_history(self):
        agent = self.make_agent()
        agent.plan_only = True
        agent.messages = [{"role": "system", "content": "rules"},
                          {"role": "user", "content": "environment"},
                          {"role": "user", "content": "fix my bug"}]
        captured = {}
        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kwargs: captured.update(kwargs) or [])))
        agent._chat()
        self.assertEqual(captured["tools"], [])
        self.assertIn("planning-only turn", captured["messages"][-1]["content"])
        self.assertEqual(agent.messages[-1]["content"], "fix my bug")
        self.assertEqual(len(agent.messages), 3)

    def test_stream_callback_receives_live_text_chunks(self):
        agent = self.make_agent()
        def chunk(text):
            delta = types.SimpleNamespace(content=text, tool_calls=None)
            return types.SimpleNamespace(usage=None, choices=[types.SimpleNamespace(delta=delta)])
        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kwargs: [chunk("Thinking "), chunk("now")])))
        received = []
        agent.stream_callback = received.append
        _, text, _ = agent._chat()
        self.assertEqual(text, "Thinking now")
        self.assertEqual(received, ["Thinking ", "now"])

    def test_one_413_retry_only_and_surface_persistent_failure(self):
        agent = self.make_agent()
        agent.messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "environment"},
            {"role": "assistant", "content": "earlier context"},
            {"role": "user", "content": "current request"},
        ]
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            raise TooLargeError("still too large")

        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
        with self.assertRaises(TooLargeError):
            agent._chat()
        self.assertEqual(len(calls), 2)
        self.assertEqual(agent.messages[-1]["content"], "current request")


if __name__ == "__main__":
    unittest.main()
