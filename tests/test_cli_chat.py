import io
import unittest
from unittest.mock import patch

from rich.console import Console

from niji.cli import _interactive_chat


class FakeAgent:
    def __init__(self):
        self.requests = []
        self.request_seconds = 0

    def chat(self, value):
        self.requests.append(value)

    def cost_line(self):
        return "turns=1 prompt_tokens=2 completion_tokens=3"


class InteractiveChatTests(unittest.TestCase):
    def test_user_message_is_written_above_pinned_composer(self):
        output = io.StringIO()
        console = Console(file=output, force_terminal=False, color_system=None)
        agent = FakeAgent()
        provider = {"provider": "groq", "model": "test-model"}
        with patch("niji.cli.Console", return_value=console), \
             patch("niji.chat_prompt.read_chat_prompt", side_effect=["meri pehli line", None]):
            _interactive_chat(agent, provider)
        self.assertIn("you ❯ meri pehli line", output.getvalue())
        self.assertIn("niji ❯", output.getvalue())
        self.assertEqual(agent.requests, ["meri pehli line"])


if __name__ == "__main__":
    unittest.main()
