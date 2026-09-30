import os
import pty
import select
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

# The package tests exercise Agent with deterministic mock clients; OpenAI itself
# is an optional test-environment install, not needed for this suite.
if "openai" not in sys.modules:
    fake_openai_module = types.ModuleType("openai")
    fake_openai_module.OpenAI = lambda **options: types.SimpleNamespace(options=options)
    sys.modules["openai"] = fake_openai_module

from niji.agent import Agent
from niji.cli import _show_provider_error
from niji.terminal_picker import _escape_key


class FakeStatusError(Exception):
    def __init__(self, status, message, headers=None):
        super().__init__(message)
        self.status_code = status
        self.body = message
        self.response = types.SimpleNamespace(headers=headers or {})


def make_agent(**kwargs):
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda **options: types.SimpleNamespace(options=options)
    with patch.dict(sys.modules, {"openai": fake_openai}):
        return Agent({"provider": "test", "model": "m", "api_key": "k",
                      "base_url": "https://example.test/v1"}, verbose=False, **kwargs)


class PickerTests(unittest.TestCase):
    def test_arrow_sequences_support_csi_ss3_and_modified_csi(self):
        cases = [([b"[", b"A"], "UP"),
                 ([b"O", b"B"], "DOWN"),
                 ([b"[", b"1", b";", b"5", b"A"], "UP")]
        for sequence, expected in cases:
            with self.subTest(sequence=sequence), \
                 patch("niji.terminal_picker.select.select", return_value=([10], [], [])), \
                 patch("niji.terminal_picker.os.read", side_effect=sequence):
                self.assertEqual(_escape_key(10), expected)

    @unittest.skipUnless(os.name == "posix", "PTY arrow test needs POSIX")
    def test_arrow_up_selects_previous_choice_in_real_pty(self):
        source_root = str(Path(__file__).resolve().parents[1] / "src")
        env = dict(os.environ, PYTHONPATH=source_root, TERM="xterm-256color")
        code = (
            "from niji.terminal_picker import arrow_select; "
            "print('RESULT=' + str(arrow_select('Choose provider', "
            "[('first','First'),('second','Second')], 1)), flush=True)"
        )
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(sys.executable, [sys.executable, "-c", code], env)
        output = bytearray()
        try:
            deadline = time.monotonic() + 8
            sent = False
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.15)
                if not ready:
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if not sent and b"Enter select" in output:
                    os.write(fd, b"\x1b[A\r")
                    sent = True
                if b"RESULT=first" in output:
                    break
            _, status = os.waitpid(pid, os.WNOHANG)
            self.assertTrue(sent, output.decode(errors="replace")[-1200:])
            self.assertIn(b"RESULT=first", output, output.decode(errors="replace")[-1200:])
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


class RetryAndBudgetTests(unittest.TestCase):
    def test_sdk_retries_disabled_and_timeout_is_finite(self):
        agent = make_agent()
        self.assertEqual(agent.client.options["max_retries"], 0)
        self.assertEqual(agent.client.options["timeout"], 120)

    def test_execution_limits_are_clamped_to_hard_caps(self):
        agent = make_agent(max_turns=1000, max_tool_calls=1000,
                           max_tool_calls_per_turn=1000)
        self.assertEqual(agent.max_turns, 100)
        self.assertEqual(agent.max_tool_calls, 100)
        self.assertEqual(agent.max_tool_calls_per_turn, 20)

    def test_deterministic_404_is_not_retried(self):
        agent = make_agent()
        calls = []
        def fail(**kwargs):
            calls.append(1)
            raise FakeStatusError(404, "model missing")
        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fail)))
        with self.assertRaises(FakeStatusError):
            agent._api_call(model="m")
        self.assertEqual(len(calls), 1)

    def test_transient_503_is_retried_once_and_only_once(self):
        agent = make_agent()
        calls = []
        def fail(**kwargs):
            calls.append(1)
            raise FakeStatusError(503, "overloaded")
        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fail)))
        with patch("niji.agent.time.sleep") as sleep, patch("niji.agent.random.uniform", return_value=0.1):
            with self.assertRaises(FakeStatusError):
                agent._api_call(model="m")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once()

    def test_long_retry_after_is_deferred_not_retried_early(self):
        agent = make_agent()
        calls = []
        def fail(**kwargs):
            calls.append(1)
            raise FakeStatusError(429, "rate limited", {"retry-after": "60"})
        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fail)))
        with patch("niji.agent.time.sleep") as sleep:
            with self.assertRaises(FakeStatusError):
                agent._api_call(model="m")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_quota_429_is_not_retried(self):
        agent = make_agent()
        calls = []
        def fail(**kwargs):
            calls.append(1)
            raise FakeStatusError(429, "insufficient quota; check billing")
        agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fail)))
        with self.assertRaises(FakeStatusError):
            agent._api_call(model="m")
        self.assertEqual(len(calls), 1)

    def test_request_tool_cap_prevents_extra_execution(self):
        agent = make_agent(max_tool_calls=1, max_tool_calls_per_turn=1)
        first_msg = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "name": "todo_read", "args": {}},
            {"id": "2", "name": "todo_read", "args": {}},
        ]}
        agent._chat = lambda: (first_msg, "", first_msg["tool_calls"])
        with patch("niji.agent.maybe_compact", side_effect=lambda messages, *a: (messages, False)), \
             patch.object(agent, "_print"):
            result = agent._loop()
        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        self.assertEqual(agent._request_tool_calls, 1)
        self.assertEqual(len(tool_messages), 2)
        self.assertIn("not executed", tool_messages[1]["content"])
        self.assertIn("safety limit", result)

    def test_failed_initial_prompt_is_removed_from_history(self):
        agent = make_agent()
        original = list(agent.messages)
        with patch.object(agent, "_loop", side_effect=TimeoutError("offline")), \
             patch.object(agent, "_save_session"):
            with self.assertRaises(TimeoutError):
                agent.chat("try this task")
        self.assertEqual(agent.messages, original)


class ProviderErrorGuidanceTests(unittest.TestCase):
    def test_quota_error_explains_not_to_retry_and_never_shows_secret(self):
        output = __import__("io").StringIO()
        error = FakeStatusError(429, "insufficient quota; check billing key=sekrit")
        with patch("niji.cli.Console", return_value=Console(file=output, color_system=None, stderr=True)):
            _show_provider_error({"provider": "test", "api_key": "sekrit"}, error)
        text = output.getvalue()
        self.assertIn("will not fix", text)
        self.assertIn("billing", text)
        self.assertNotIn("sekrit", text)

    def test_404_guides_model_change_without_blind_retry(self):
        output = __import__("io").StringIO()
        error = FakeStatusError(404, "model not found")
        with patch("niji.cli.Console", return_value=Console(file=output, color_system=None, stderr=True)):
            _show_provider_error({"provider": "test", "model": "old", "api_key": "k"}, error)
        self.assertIn("do not retry the same ID blindly", output.getvalue())
        self.assertIn("/model", output.getvalue())


if __name__ == "__main__":
    unittest.main()
