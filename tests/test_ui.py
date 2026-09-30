import io
import unittest

from rich.console import Console

from niji.ui import render_home


class DummyAgent:
    session_id = "20260930-example-session-123456"
    approval = "ask"
    mcp_clients = [object()]
    tool_schemas = [
        {"function": {"name": "read_file"}},
        {"function": {"name": "edit_file"}},
        {"function": {"name": "run_shell"}},
    ]


class DashboardTests(unittest.TestCase):
    def render(self, width):
        output = io.StringIO()
        console = Console(file=output, width=width, color_system=None, force_terminal=False)
        render_home(DummyAgent(), {
            "provider": "nvidia", "model": "z-ai/glm-5.3-flash"
        }, console=console)
        return output.getvalue()

    def test_brand_and_live_session_details(self):
        output = self.render(72)
        for expected in ("N I J I", "PERSONAL AI WORKSPACE", "LIVE SESSION",
                         "z-ai/glm-5.3-flash", "CONFIRM ACTIONS", "read_file",
                         "3 active tools", "/help commands"):
            self.assertIn(expected, output)

    def test_narrow_terminal_stays_within_phone_width(self):
        output = self.render(56)
        self.assertIn("N I J I", output)
        self.assertTrue(all(len(line) <= 56 for line in output.splitlines()),
                        "dashboard overflowed the narrow terminal")

    def test_wide_terminal_renders_brand_and_session_side_by_side(self):
        output = self.render(120)
        self.assertIn("N I J I", output)
        self.assertIn("LIVE SESSION", output)
        self.assertIn("WORKSPACE", output)

    def test_quiet_mode_renders_nothing(self):
        output = io.StringIO()
        console = Console(file=output, width=72, color_system=None)
        render_home(DummyAgent(), {"provider": "test", "model": "test-model"},
                    quiet=True, console=console)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
