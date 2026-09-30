import os
import pty
import select
import sys
import time
import unittest
from pathlib import Path

from niji.chat_prompt import _fields, _prompt_lines, _strip_ansi


class DummyAgent:
    model = "deepseek-v4-pro"
    provider_name = "groq"
    started_at = time.monotonic() - 4
    request_seconds = 0
    usage = {"prompt_tokens": 1234, "completion_tokens": 321}
    messages = [{"role": "user"}]
    tool_usage = {"read_file": 3, "bash": 2}


class ChatPromptTests(unittest.TestCase):
    def setUp(self):
        self.agent = DummyAgent()
        self.provider = {"provider": "groq", "model": "deepseek-v4-pro"}

    def test_footer_contains_screenshot_fields_and_real_session_values(self):
        rows, _, _ = _prompt_lines(self.agent, self.provider, "", 0, 160, enabled=False)
        rendered = "\n".join(rows)
        for expected in ("MODEL", "deepseek-v4-pro", "PROVIDER", "groq", "CONTEXT",
                         "AGENT", "Niji-Agent", "RUNTIME", "Python", "TOKENS",
                         "1,555", "TOOLS", "5", "TIME"):
            self.assertIn(expected, rendered)

    def test_brand_input_and_footer_fit_narrow_and_wide_terminals(self):
        for width in (48, 56, 80, 120, 180):
            with self.subTest(width=width):
                rows, cursor, _ = _prompt_lines(self.agent, self.provider, "hello", 5, width, enabled=False)
                self.assertEqual(cursor, 8)
                self.assertTrue(all(len(line) <= width - 1 for line in rows))
                self.assertIn("NIJI", rows[0])
                self.assertTrue(any("deepseek-v4-pro" in line for line in rows))

    def _run_pty_prompt(self, chunks, expected):
        pid, fd = pty.fork()
        if pid == 0:
            root = Path(__file__).resolve().parents[1]
            sys.path.insert(0, str(root / "src"))
            from niji.chat_prompt import read_chat_prompt
            class Agent:
                started_at = time.monotonic()
                usage = {"prompt_tokens": 4, "completion_tokens": 2}
                messages = []
                tool_usage = {"bash": 1}
            value = read_chat_prompt(Agent(), {"provider": "groq", "model": "test-model"})
            print("RESULT=" + repr(value), flush=True)
            os._exit(0)

        output = bytearray()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and b"SESSION DETAILS" not in output:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if ready:
                    output.extend(os.read(fd, 4096))
            self.assertIn(b"SESSION DETAILS", output)
            for chunk in chunks:
                os.write(fd, chunk)
                time.sleep(0.15)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and b"RESULT=" not in output:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if ready:
                    try:
                        output.extend(os.read(fd, 4096))
                    except OSError:
                        break
            self.assertIn(expected.encode(), output)
            self.assertIn(b"MODEL", output)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def test_interactive_composer_accepts_typing_and_backspace_in_a_pty(self):
        self._run_pty_prompt([b"hellx", b"\x7f", b"o\r"], "RESULT='hello'")

    def test_interactive_composer_handles_bracketed_clipboard_paste(self):
        self._run_pty_prompt([b"\x1b[200~hello world\x1b[201~", b"\r"], "RESULT='hello world'")

    def test_control_d_exits_cleanly_from_empty_prompt(self):
        self._run_pty_prompt([b"\x04"], "RESULT=None")


if __name__ == "__main__":
    unittest.main()
