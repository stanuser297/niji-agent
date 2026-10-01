import hashlib
import json
import os
import threading
import time
import unittest
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from unittest.mock import patch

from niji.webui import NijiWebUI


class FakeAgent:
    def __init__(self, require_approval=False):
        self.provider_cfg = {"provider": "test", "model": "demo-model", "api_key": "private-test-secret"}
        self.provider_name = "test"
        self.model = "demo-model"
        self.client = object()
        self.session_id = "test-session"
        self.approval = "ask"
        self.approval_callback = None
        self.activity_callback = None
        self.activity = [{"time": "12:00:00", "level": "READY", "message": "Ready"}]
        self._activity_lock = threading.RLock()
        self.usage = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.tool_usage = {}
        self.tool_schemas = [{"function": {"name": "read_file"}}]
        self.messages = [
            {"role": "system", "content": "private system instructions"},
            {"role": "user", "content": "[Environment: test]"},
        ]
        self.max_turns = 10
        self.max_tool_calls = 30
        self.max_tool_calls_per_turn = 6
        self.require_approval = require_approval
        self.tool_policies = {}
        self.last_plan_only = False
        self.pause_stream = False
        self.stream_ready = threading.Event()
        self.finish_stream = threading.Event()
        self.cancel_requested = threading.Event()

    def cancel(self):
        self.cancel_requested.set()
        self.finish_stream.set()

    def resume(self, messages):
        self.messages = list(messages)

    def _record_activity(self, level, message):
        self.activity.append({"time": "12:01:00", "level": level, "message": message})

    def chat(self, message):
        self.last_plan_only = bool(getattr(self, "plan_only", False))
        if self.activity_callback:
            self.activity_callback({"time": "12:02:00", "level": "THINKING", "message": "Thinking on it"})
        self.messages.append({"role": "user", "content": message})
        if self.require_approval:
            approved = self.approval_callback("write_file", {"path": "notes.txt"})
            answer = "approved" if approved else "denied"
        elif self.last_plan_only:
            answer = "1. Inspect the project\\n2. Run the tests"
        else:
            answer = "Hello from Niji: " + message
        if self.stream_callback:
            self.stream_callback(answer[:10])
            self.stream_ready.set()
            if self.pause_stream:
                self.finish_stream.wait(2)
            if not self.cancel_requested.is_set():
                self.stream_callback(answer[10:])
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
        self.assertIn(".chatcard{background:transparent;border:0;border-radius:0;box-shadow:none}", page)
        self.assertIn(".message{width:fit-content;max-width:min(88%,840px);border:0;border-radius:0;background:transparent;padding:0;", page)
        self.assertIn(".message.user{align-self:flex-end;background:transparent;border-color:transparent}", page)
        self.assertIn("body.light .chatcard{background:transparent}", page)
        self.assertIn(".chathead{display:none}", page)
        self.assertIn('id="attach-button"', page)
        self.assertIn('id="github-button"', page)
        self.assertIn('id="reasoning-level"', page)
        self.assertIn('id="mic-button"', page)
        self.assertIn('id="file-input"', page)
        self.assertIn("el('mic-button').onclick=toggleDictation", page)
        self.assertIn("addAttachments(files)", page)
        self.assertIn("Public GitHub repository reference", page)
        self.assertIn("window.SpeechRecognition", page)
        self.assertIn("File is over 64 KB", page)
        self.assertIn("X-Niji-Token", page)
        self.assertIn("RECENT THREADS", page)
        self.assertIn('id="view-overview"', page)
        self.assertIn('id="view-tools"', page)
        self.assertIn('id="view-settings"', page)
        self.assertIn('id="settings-detail-slot"', page)
        self.assertIn('id="settings-chat-preferences"', page)
        self.assertIn('id="settings-tools-slot"', page)
        self.assertNotIn('data-view="overview"', page)
        self.assertNotIn('data-view="tools"', page)
        self.assertNotIn('data-view="activity"', page)
        self.assertIn('id="enter-send-toggle"', page)
        self.assertIn("moveSettingsSections", page)
        self.assertIn("niji-enter-to-send", page)
        self.assertIn("niji-plan-first", page)
        self.assertIn("e.key===','", page)
        self.assertIn("Thinking on it", page)
        self.assertIn("Mapping it out", page)
        self.assertIn('id="plan-only"', page)
        self.assertIn('id="send"', page)
        self.assertIn("Stop ■", page)
        self.assertIn("setChatControls", page)
        self.assertIn("stop-send", page)
        self.assertIn("Send ↗", page)
        self.assertNotIn('id="stop-job"', page)
        self.assertNotIn('id="live-progress"', page)
        self.assertNotIn(".live-progress", page)
        self.assertIn("#settings-chat-preferences .composerfoot", page)
        self.assertNotIn(".settings-chat-preferences .composerfoot", page)
        self.assertNotIn('id="working-status"', page)
        self.assertNotIn('id="working-text"', page)
        self.assertIn("placeholder?.querySelector('.workmeta')", page)
        self.assertIn("className='workmeta'", page)
        self.assertIn("box.classList.add('working','live-status')", page)
        self.assertIn(".message.live-status .msglabel{display:none}", page)
        self.assertIn(".message.live-status .msgbody{display:none}", page)
        self.assertIn(".message.live-status:before", page)
        self.assertIn("Thinking…", page)
        self.assertIn("workdetail", page)
        self.assertIn("Understanding your request and choosing a next step.", page)
        self.assertIn("toolDescriptions", page)
        self.assertIn("Planning…", page)
        self.assertIn("Searching the web…", page)
        self.assertIn("Running tests…", page)
        self.assertIn("body.textContent=j.streamed||''", page)
        self.assertIn("placeholder.classList.toggle('has-stream',!!j.streamed)", page)
        self.assertIn("pollFailures++", page)
        self.assertIn("Math.min(4000,380*Math.pow(2", page)
        self.assertIn("retrying its status check", page)
        self.assertIn("setTimeout(()=>watchJob(id,false,planOnly),1000)", page)
        self.assertIn(".message.live-status.has-stream .msgbody{display:block", page)
        self.assertNotIn("provider}/${model} · turn", page)
        self.assertNotIn("j.streamed||j.progress_detail", page)
        self.assertIn('id="file-changes"', page)
        self.assertIn("Ask every time", page)

    def test_state_endpoint_requires_token_and_never_returns_api_key(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.request("/api/state")
        self.assertEqual(missing.exception.code, 403)
        state = json.loads(self.request("/api/state", token=self.ui.token).read())
        self.assertEqual(state["model"], "demo-model")
        self.assertEqual(state["tools"][0]["name"], "read_file")
        self.assertEqual(state["tools"][0]["access"], "Read-only")
        self.assertIn("workspace", state["runtime"])
        self.assertIn("transcript", state)
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
        self.assertEqual(job["streamed"], "Hello from Niji: hello")
        self.assertTrue(job["plan_only"] is False)

    def test_cancelled_provider_exception_is_reported_as_cancelled(self):
        job_id = "cancelled-error-case"
        self.ui._jobs[job_id] = {
            "id": job_id, "status": "running", "response": "", "error": "",
            "streamed": "partial output", "cancel_requested": True,
        }
        self.ui._active_job = job_id
        self.ui._busy = True
        def raise_after_cancel(message):
            raise RuntimeError("provider stream closed during cancellation")
        self.agent.chat = raise_after_cancel
        self.ui._run_job(job_id, "stop this")
        job = self.ui._jobs[job_id]
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["response"], "partial output")
        self.assertEqual(job["error"], "")

    def test_streamed_text_is_available_before_completion_and_stop_is_cooperative(self):
        self.agent.pause_stream = True
        started = json.loads(self.request("/api/chat", {"message": "slow response"}, self.ui.token).read())
        self.assertTrue(self.agent.stream_ready.wait(2))
        job = json.loads(self.request("/api/jobs/" + started["id"], token=self.ui.token).read())
        self.assertEqual(job["status"], "running")
        self.assertTrue(job["streamed"])
        self.assertEqual(job["progress"], "Writing the response")
        response = self.request("/api/jobs/" + started["id"] + "/cancel", {}, self.ui.token)
        self.assertEqual(response.status, 202)
        deadline = time.time() + 3
        while time.time() < deadline:
            job = json.loads(self.request("/api/jobs/" + started["id"], token=self.ui.token).read())
            if job["status"] != "running":
                break
            time.sleep(0.03)
        self.assertEqual(job["status"], "cancelled")

    def test_plan_only_does_not_execute_tools_and_is_visible_in_job(self):
        started = json.loads(self.request("/api/chat", {"message": "inspect project", "plan_only": True}, self.ui.token).read())
        deadline = time.time() + 3
        while time.time() < deadline:
            job = json.loads(self.request("/api/jobs/" + started["id"], token=self.ui.token).read())
            if job["status"] != "running":
                break
            time.sleep(0.03)
        self.assertEqual(job["status"], "completed")
        self.assertTrue(job["plan_only"])
        self.assertTrue(self.agent.last_plan_only)
        self.assertIn("Inspect the project", job["response"])

    def test_session_tool_policy_can_be_changed(self):
        state = json.loads(self.request("/api/tool-policy", {"name": "read_file", "policy": "block"}, self.ui.token).read())
        self.assertEqual(state["tool_policies"]["read_file"], "block")
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request("/api/tool-policy", {"name": "not_a_tool", "policy": "allow"}, self.ui.token).read()
        self.assertEqual(invalid.exception.code, 400)

    def test_workspace_profiles_switch_folder_and_reload_project_guidance(self):
        old_cwd = Path.cwd()
        self.addCleanup(os.chdir, old_cwd)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "project"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Use unittest for this project.")
            with patch("niji.webui.CONFIG_DIR", base / "config"), \
                 patch("niji.webui._PROFILE_FILE", base / "config" / "project_profiles.json"):
                saved = json.loads(self.request("/api/profiles", {
                    "action": "save", "name": "demo", "path": str(workspace)}, self.ui.token).read())
                self.assertEqual(saved["profiles"][0]["name"], "demo")
                active = json.loads(self.request("/api/profiles", {
                    "action": "activate", "name": "demo"}, self.ui.token).read())
                self.assertEqual(active["workspace"], str(workspace.resolve()))
                state = json.loads(self.request("/api/state", token=self.ui.token).read())
                self.assertTrue(state["runtime"]["project_guidance"])
                self.assertIn("Use unittest", self.agent.messages[0]["content"])
        os.chdir(old_cwd)

    def test_file_diff_endpoint_returns_unified_diff_and_redacts_provider_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("new private-test-secret value\n")
            self.agent.file_change_history = [{
                "path": str(path), "before": b"old value\n",
                "after_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "operation": "write",
            }]
            result = json.loads(self.request("/api/changes/0", token=self.ui.token).read())
            self.assertIn("-old value", result["diff"])
            self.assertIn("+new [redacted] value", result["diff"])
            self.assertNotIn("private-test-secret", result["diff"])

    def test_file_diff_endpoint_rejects_symlink_and_bad_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            target.write_text("content")
            link = Path(tmp) / "link.txt"
            link.symlink_to(target)
            self.agent.file_change_history = [{"path": str(link), "before": b"", "operation": "write"}]
            with self.assertRaises(urllib.error.HTTPError) as unsafe:
                self.request("/api/changes/0", token=self.ui.token).read()
            self.assertEqual(unsafe.exception.code, 400)
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                self.request("/api/changes/abc", token=self.ui.token).read()
            self.assertEqual(invalid.exception.code, 400)

    def test_profile_storage_error_returns_json_not_dropped_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "project"
            workspace.mkdir()
            blocked = base / "not-a-directory"
            blocked.write_text("block")
            with patch("niji.webui.CONFIG_DIR", blocked), \
                 patch("niji.webui._PROFILE_FILE", blocked / "profiles.json"):
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    self.request("/api/profiles", {
                        "action": "save", "name": "demo", "path": str(workspace)}, self.ui.token).read()
                self.assertEqual(failure.exception.code, 400)
                payload = json.loads(failure.exception.read())
                self.assertIn("Could not save workspace profiles", payload["error"])

    def test_browser_memory_controls_read_replace_and_clear_private_notes(self):
        with tempfile.TemporaryDirectory() as tmp, patch("niji.webui.MEMORY_FILE", Path(tmp) / "MEMORY.md"):
            initial = json.loads(self.request("/api/memory", token=self.ui.token).read())
            self.assertEqual(initial["content"], "")
            saved = self.request("/api/memory", {"action": "replace", "content": "Use pytest."}, self.ui.token).read()
            self.assertIn(b"Use pytest", saved)
            self.assertEqual((Path(tmp) / "MEMORY.md").read_text(), "Use pytest.")
            self.request("/api/memory", {"action": "clear", "content": ""}, self.ui.token).read()
            self.assertFalse((Path(tmp) / "MEMORY.md").exists())

    def test_browser_context_compaction_keeps_the_latest_request(self):
        self.agent.messages.extend([
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer " * 500},
            {"role": "user", "content": "current question"},
        ])
        result = json.loads(self.request("/api/compact", {}, self.ui.token).read())
        self.assertTrue(result["changed"])
        self.assertEqual(self.agent.messages[-1]["content"], "current question")
        self.assertLess(result["after"], result["before"])

    def test_current_session_export_omits_internal_messages(self):
        self.agent.messages.extend([
            {"role": "user", "content": "export this"},
            {"role": "assistant", "content": "visible reply"},
            {"role": "tool", "content": "private tool output"},
        ])
        data = json.loads(self.request("/api/sessions/test-session/export", token=self.ui.token).read())
        self.assertEqual([m["role"] for m in data["messages"]], ["user", "assistant"])
        self.assertNotIn("private system instructions", json.dumps(data))
        self.assertNotIn("private tool output", json.dumps(data))

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

    def test_new_thread_resets_session_usage_and_transcript(self):
        self.agent.messages.extend([
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ])
        self.agent.usage["turns"] = 4
        state = json.loads(self.request("/api/session/new", {}, self.ui.token).read())
        self.assertNotEqual(state["session_id"], "test-session")
        self.assertEqual(state["usage"]["turns"], 0)
        self.assertEqual(state["transcript"], [])

    def test_saved_sessions_can_be_listed_and_opened(self):
        with tempfile.TemporaryDirectory() as tmp, patch("niji.webui.SESSION_DIR", Path(tmp)):
            session = Path(tmp) / "saved-session.json"
            session.write_text(json.dumps([
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "Find the latest release"},
                {"role": "assistant", "content": "Here is the release"},
            ]))
            listed = json.loads(self.request("/api/sessions", token=self.ui.token).read())
            self.assertEqual(listed["sessions"][0]["id"], "saved-session")
            self.assertEqual(listed["sessions"][0]["title"], "Find the latest release")
            state = json.loads(self.request("/api/sessions/saved-session", {}, self.ui.token).read())
            self.assertEqual(state["session_id"], "saved-session")
            self.assertEqual([m["role"] for m in state["transcript"]], ["user", "assistant"])

    def test_approval_mode_can_be_changed_for_the_ui_session(self):
        state = json.loads(self.request("/api/settings", {"approval": "auto"}, self.ui.token).read())
        self.assertEqual(state["approval"], "auto")
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request("/api/settings", {"approval": "unsafe"}, self.ui.token).read()
        self.assertEqual(invalid.exception.code, 400)

    def test_listener_refuses_network_binding(self):
        with self.assertRaises(ValueError):
            NijiWebUI(FakeAgent(), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
