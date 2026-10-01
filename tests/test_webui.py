import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from niji.webui import NijiWebUI


class FakeAgent:
    def __init__(self, require_approval=False):
        self.provider_cfg = {"provider": "test", "model": "demo-model", "api_key": "private-test-secret"}
        self.provider_name = "test"
        self.model = "demo-model"
        self.session_id = "test-session"
        self.approval = "ask"
        self.approval_callback = None
        self.activity_callback = None
        self.activity = [{"time": "12:00:00", "level": "READY", "message": "Ready"}]
        self._activity_lock = threading.RLock()
        self.usage = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.tool_usage = {}
        self.tool_schemas = [{"function": {"name": "read_file"}}]
        self.messages = [{"role": "system", "content": "private system instructions"}]
        self.max_turns = 10
        self.max_tool_calls = 30
        self.max_tool_calls_per_turn = 6
        self.require_approval = require_approval

    def chat(self, message):
        self.messages.append({"role": "user", "content": message})
        if self.require_approval:
            approved = self.approval_callback("write_file", {"path": "notes.txt"})
            answer = "approved" if approved else "denied"
        else:
            answer = "Hello from Niji: " + message
        self.messages.append({"role": "assistant", "content": answer})
        self.usage["turns"] += 1
        return answer


class WebUITests(unittest.TestCase):
    def test_cli_dispatches_ui_subcommand(self):
        from niji.cli import main
        with patch("niji.cli.sys.argv", ["niji", "ui", "--port", "0"]):
            with patch("niji.cli._cmd_ui") as cmd:
                main()
        cmd.assert_called_once_with(["--port", "0"])

    def setUp(self):
        self.agent = FakeAgent()
        self.ui = NijiWebUI(self.agent, port=0)
        self.thread = threading.Thread(target=self.ui.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.ui.httpd.server_port}"

    def tearDown(self):
        self.ui.close()
        self.thread.join(timeout=2)

    def request(self, path, data=None, token=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {}
        if token is not None:
            headers["X-Niji-Token"] = token
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=headers)
        return urllib.request.urlopen(req, timeout=3)

    def test_page_requires_private_one_time_token(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(self.base + "/", timeout=3)
        self.assertEqual(missing.exception.code, 403)
        response = urllib.request.urlopen(self.ui.url, timeout=3)
        page = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("NIJI AGENT", page)
        self.assertIn("X-Niji-Token", page)

    def test_state_endpoint_requires_token_and_never_returns_api_key(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.request("/api/state")
        self.assertEqual(missing.exception.code, 403)
        state = json.loads(self.request("/api/state", token=self.ui.token).read())
        self.assertEqual(state["model"], "demo-model")
        self.assertIn("read_file", state["tools"])
        self.assertNotIn("private-test-secret", json.dumps(state))
        self.assertNotIn("private system instructions", json.dumps(state))

    def test_chat_runs_as_background_job_and_returns_response(self):
        started = json.loads(self.request("/api/chat", {"message": "hello"}, self.ui.token).read())
        deadline = time.time() + 3
        job = None
        while time.time() < deadline:
            job = json.loads(self.request("/api/jobs/" + started["id"], token=self.ui.token).read())
            if job["status"] != "running":
                break
            time.sleep(0.03)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["response"], "Hello from Niji: hello")

    def test_tool_approval_is_delivered_to_the_browser(self):
        self.agent.require_approval = True
        started = json.loads(self.request("/api/chat", {"message": "write a note"}, self.ui.token).read())
        deadline = time.time() + 3
        state = None
        while time.time() < deadline:
            state = json.loads(self.request("/api/state", token=self.ui.token).read())
            if state["pending_approvals"]:
                break
            time.sleep(0.03)
        self.assertTrue(state["pending_approvals"])
        approval = state["pending_approvals"][0]
        self.request("/api/approvals/" + approval["id"], {"approved": True}, self.ui.token).read()
        deadline = time.time() + 3
        job = None
        while time.time() < deadline:
            job = json.loads(self.request("/api/jobs/" + started["id"], token=self.ui.token).read())
            if job["status"] != "running":
                break
            time.sleep(0.03)
        self.assertEqual(job["response"], "approved")

    def test_listener_refuses_network_binding(self):
        with self.assertRaises(ValueError):
            NijiWebUI(FakeAgent(), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
