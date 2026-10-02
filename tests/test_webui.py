import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import unittest
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from unittest.mock import patch

from niji.webui import NijiWebUI
from niji.webui_frontend import PAGE
from niji.planning import save_plan as REAL_SAVE_PLAN


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
        self.approved_plan_seen = None
        self.leave_plan_incomplete = False
        self.corrupt_completion_evidence = False
        self.todos = {"items": []}
        self.plan_callback = None
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
        approved_plan = getattr(self, "approved_plan", None)
        self.approved_plan_seen = list(approved_plan) if approved_plan is not None else None
        if self.activity_callback:
            self.activity_callback({"time": "12:02:00", "level": "THINKING", "message": "Thinking on it"})
        self.messages.append({"role": "user", "content": message})
        if approved_plan is not None and not self.leave_plan_incomplete:
            from niji.tools import dispatch
            for index in range(len(approved_plan)):
                active = [dict(item) for item in self.todos["items"]]
                active[index]["status"] = "in_progress"
                started = dispatch("todo_write", {"todos": active, "activeForm": "Working"},
                                   {"agent": self, "todos": self.todos})
                if str(started).startswith("[error]"):
                    raise AssertionError(started)
                completed = [dict(item) for item in self.todos["items"]]
                completed[index]["status"] = "completed"
                completed[index]["evidence"] = "Fixture observed a successful step result."
                result = dispatch("todo_write", {"todos": completed, "activeForm": "Verified"},
                                  {"agent": self, "todos": self.todos})
                if str(result).startswith("[error]"):
                    raise AssertionError(result)
            if self.corrupt_completion_evidence and self.todos["items"]:
                self.todos["items"][-1]["evidence"] = "ok"
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
        self._plan_load_patch = patch("niji.webui.load_plan", return_value=[])
        self._plan_save_patch = patch("niji.webui.save_plan")
        self._stateful_plan_save_patch = patch("niji.planning.save_plan")
        self._plan_load_patch.start()
        self._plan_save_patch.start()
        self._stateful_plan_save_patch.start()
        self.ui = NijiWebUI(self.agent, port=0)
        self.thread = threading.Thread(target=self.ui.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.ui.httpd.server_port}"

    def tearDown(self):
        self.ui.close()
        self.thread.join(timeout=2)
        self._plan_load_patch.stop()
        self._plan_save_patch.stop()
        self._stateful_plan_save_patch.stop()

    def request(self, path, data=None, token=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {}
        if token is not None:
            headers["X-Niji-Token"] = token
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, headers=headers)
        return urllib.request.urlopen(req, timeout=3)

    def test_plan_editor_exposes_optional_completion_criteria(self):
        self.assertIn("Optional completion criteria", PAGE)
        self.assertIn("acceptance_criteria:x.acceptance_criteria.trim()", PAGE)
        self.assertIn("maxLength=400", PAGE)

    def test_plan_editor_reordering_preserves_prerequisite_order(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        start = PAGE.index("function canMovePlanStep(")
        end = PAGE.index("\nfunction openPlanEditor", start)
        helper = PAGE[start:end]
        script = helper + """
const rows = [
  {id:'inspect', depends_on:[]},
  {id:'implement', depends_on:['inspect']},
  {id:'verify', depends_on:['implement']},
  {id:'docs', depends_on:[]}
];
if (canMovePlanStep(rows, 1, 0)) throw new Error('dependent step moved before prerequisite');
if (canMovePlanStep(rows, 0, 1)) throw new Error('prerequisite moved after dependent');
if (!canMovePlanStep(rows, 3, 2)) throw new Error('independent step could not be reordered');
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_plan_renderer_shows_dependency_waiting_state_safely(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        start = PAGE.index("function buildTaskPlanList(")
        end = PAGE.index("\nfunction renderJobPlan", start)
        renderer = PAGE[start:end]
        script = renderer + """
class FakeNode {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.className='';this.textContent=''}
  append(...nodes){this.children.push(...nodes)}
  setAttribute(key,value){this.attributes[key]=value}
}
global.document={createElement:(tag)=>new FakeNode(tag)};
const tree=buildTaskPlanList([
  {id:'inspect',content:'Inspect <source>',status:'pending',acceptance_criteria:'Only current files are reviewed.'},
  {id:'build',content:'Build safely',status:'pending',depends_on:['inspect'],evidence:'Build completed without errors.'}
]);
function walk(node){return [node,...node.children.flatMap(walk)]}
const nodes=walk(tree);
if(!nodes.some(n=>n.className==='task-plan-deps waiting' && n.textContent==='Waiting for: Inspect <source>')) throw new Error('waiting dependency label missing');
if(!nodes.some(n=>n.className==='task-plan-state pending')) throw new Error('explicit pending status missing');
if(!nodes.some(n=>n.className==='task-plan-criteria' && n.textContent==='Check: Only current files are reviewed.')) throw new Error('acceptance criteria missing');
if(!nodes.some(n=>n.className==='task-plan-evidence' && n.textContent.includes('Build completed without errors.'))) throw new Error('completion evidence missing');
if(nodes.some(n=>n.innerHTML)) throw new Error('renderer used unsafe HTML');
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_page_requires_private_one_time_token(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(self.base + "/", timeout=3)
        self.assertEqual(missing.exception.code, 403)
        response = urllib.request.urlopen(self.ui.url, timeout=3)
        page = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("NIJI AGENT", page)
        self.assertNotIn("\\nfunction renderSessions", page)
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
        self.assertIn('id="model-provider"', page)
        self.assertIn('id="fetch-models"', page)
        self.assertIn('id="apply-model"', page)
        self.assertIn("async function togglePinnedSession", page)
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
        self.assertIn("Preparing next step…", page)
        self.assertNotIn("Thinking…", page)
        self.assertIn("Running a command…", page)
        self.assertIn("still running", page)
        self.assertIn("duration=detail.match", page)
        self.assertIn("Planning the steps…", page)
        self.assertIn("Checking your request and deciding what action is needed.", page)
        self.assertIn("Running the requested command.", page)
        self.assertIn("Still running · ${duration}s.", page)
        self.assertIn("workdetail", page)
        self.assertIn("Checking your request and deciding what action is needed.", page)
        self.assertIn("Running the requested command.", page)
        self.assertIn("Still running · ${duration}s.", page)
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
        self.assertIn('id="connector-settings"', page)
        self.assertIn('id="connector-api-key"', page)
        self.assertIn('id="add-connector"', page)
        self.assertIn("renderJobEvents(j.events,placeholder)", page)
        self.assertIn("className='execution-steps'", page)
        self.assertIn("function renderJobPlan(items", page)
        self.assertIn("function buildTaskPlanList(items)", page)
        self.assertIn("task-plan-deps", page)
        self.assertIn("Waiting for: ", page)
        self.assertIn("function openPlanEditor(actions,run,box,sourceJobId,items,setItems)", page)
        self.assertIn("function canMovePlanStep(rows,from,to)", page)
        self.assertIn("depends_on:[...x.depends_on]", page)
        self.assertIn("aria-describedby',hint.id", page)
        self.assertIn("removing a step also removes it from prerequisite lists", page)
        self.assertIn("function renderSavedPlan(items", page)
        self.assertIn("Approve & run plan", page)
        self.assertIn("/approve-plan", page)
        self.assertIn("/edit-plan", page)
        self.assertIn("function attachPlanAction(box,sourceJobId,items=[])", page)
        self.assertIn("choose prerequisites", page)
        self.assertIn("+ Add step", page)
        self.assertIn("Step ${index+1} depends on", page)
        self.assertNotIn("Execute the approved numbered plan above", page)
        self.assertIn('id="auto-compact-toggle"', page)
        self.assertIn('id="compaction-threshold"', page)
        self.assertIn("413 emergency recovery is still enabled", page)
        self.assertIn("Ask every time", page)

    def test_browser_model_picker_and_thread_pinning_controls_are_present(self):
        page = urllib.request.urlopen(self.ui.url, timeout=3).read().decode()
        for marker in ('id="model-provider"', 'id="model-catalog"', 'id="model-manual"',
                       'id="fetch-models"', 'id="apply-model"', 'pin-thread',
                       'async function fetchModelCatalog', 'async function applyModel',
                       'async function togglePinnedSession', 'PINNED'):
            self.assertIn(marker, page)

    def test_browser_automation_manager_controls_are_present(self):
        page = urllib.request.urlopen(self.ui.url, timeout=3).read().decode()
        for marker in ('data-view="automations"', 'id="view-automations"',
                       'id="automation-form"', 'id="automation-run-at"',
                       'id="automation-plan-only"', 'async function loadAutomations',
                       'function renderAutomations', 'async function automationAction'):
            self.assertIn(marker, page)
        self.assertIn("local Niji workspace", page)
        self.assertIn("Plan-only by default", page)

    def test_automation_creation_persists_private_file_and_can_pause_delete(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            with (patch("niji.webui.CONFIG_DIR", config),
                  patch("niji.webui._AUTOMATION_FILE", config / "automations.json")):
                run_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                result = json.loads(self.request("/api/automations", {
                    "action": "create", "name": "Daily review", "prompt": "Review the latest changes",
                    "run_at": run_at, "mode": "interval", "interval_minutes": 1440,
                    "plan_only": True,
                }, self.ui.token).read())
                item = result["automations"][0]
                self.assertEqual(item["name"], "Daily review")
                self.assertTrue(item["enabled"])
                self.assertTrue(item["plan_only"])
                saved = config / "automations.json"
                self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
                result = json.loads(self.request("/api/automations", {
                    "action": "toggle", "id": item["id"], "enabled": False,
                }, self.ui.token).read())
                self.assertFalse(result["automations"][0]["enabled"])
                result = json.loads(self.request("/api/automations", {
                    "action": "delete", "id": item["id"],
                }, self.ui.token).read())
                self.assertEqual(result["automations"], [])

    def test_automation_rejects_invalid_schedule_and_task(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp, \
             patch("niji.webui._AUTOMATION_FILE", Path(tmp) / "automations.json"), \
             patch("niji.webui.CONFIG_DIR", Path(tmp)):
            invalid = [
                {"action": "create", "name": "x", "prompt": "task", "run_at": "soon", "mode": "once", "plan_only": True},
                {"action": "create", "name": "x", "prompt": "task", "run_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(), "mode": "interval", "interval_minutes": 2, "plan_only": True},
            ]
            for body in invalid:
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    self.request("/api/automations", body, self.ui.token)
                self.assertEqual(failure.exception.code, 400)

    def test_due_automation_runs_as_job_and_records_completion(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            file = config / "automations.json"
            with (patch("niji.webui.CONFIG_DIR", config),
                  patch("niji.webui._AUTOMATION_FILE", file)):
                run_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                created = json.loads(self.request("/api/automations", {
                    "action": "create", "name": "Scheduled check", "prompt": "Review staged changes",
                    "run_at": run_at, "mode": "once", "plan_only": True,
                }, self.ui.token).read())["automations"][0]
                stored = json.loads(file.read_text())
                stored[0]["next_run"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                file.write_text(json.dumps(stored))
                self.assertTrue(self.ui._dispatch_due_automations())
                self.ui._job_thread.join(timeout=3)
                result = json.loads(self.request("/api/automations", token=self.ui.token).read())
                item = next(item for item in result["automations"] if item["id"] == created["id"])
                self.assertEqual(item["last_status"], "completed")
                self.assertFalse(item["enabled"])
                self.assertTrue(self.agent.last_plan_only)
                self.assertIn("Review staged changes", [m["content"] for m in self.agent.messages])

    def test_browser_files_results_view_controls_are_present(self):
        page = urllib.request.urlopen(self.ui.url, timeout=3).read().decode()
        for marker in ('data-view="files"', 'id="view-files"', 'id="artifact-list"',
                       'id="artifact-refresh"', 'async function loadArtifacts',
                       'async function downloadArtifact', 'Preview diff'):
            self.assertIn(marker, page)

    def test_artifact_list_and_download_are_workspace_scoped(self):
        old_cwd = Path.cwd()
        self.addCleanup(os.chdir, old_cwd)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            os.chdir(root)
            artifact = root / "report.txt"
            artifact.write_text("generated result\n")
            external = Path(outside) / "secret.txt"
            external.write_text("outside workspace\n")
            link = root / "external-link.txt"
            link.symlink_to(external)
            self.agent.file_change_history = [
                {"path": str(artifact), "before": b"", "operation": "write"},
                {"path": str(external), "before": b"", "operation": "write"},
                {"path": str(link), "before": b"", "operation": "write"},
            ]
            listed = json.loads(self.request("/api/artifacts", token=self.ui.token).read())["artifacts"]
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["path"], "report.txt")
            response = self.request("/api/artifacts/0", token=self.ui.token)
            self.assertEqual(response.read(), b"generated result\n")
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            with self.assertRaises(urllib.error.HTTPError) as unsafe:
                self.request("/api/artifacts/1", token=self.ui.token)
            self.assertEqual(unsafe.exception.code, 400)

    def test_workspace_state_and_artifacts_follow_agent_workspace_not_process_cwd(self):
        old_cwd = Path.cwd()
        self.addCleanup(os.chdir, old_cwd)
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as cwd_dir:
            workspace = Path(workspace_dir).resolve()
            process_cwd = Path(cwd_dir).resolve()
            (workspace / "AGENTS.md").write_text("workspace instructions")
            artifact = workspace / "result.txt"
            artifact.write_text("from active workspace")
            decoy = process_cwd / "decoy.txt"
            decoy.write_text("not active")
            self.agent.workspace = workspace
            self.agent.file_change_history = [
                {"path": str(artifact), "operation": "write"},
                {"path": str(decoy), "operation": "write"},
            ]
            os.chdir(process_cwd)
            state = json.loads(self.request("/api/state", token=self.ui.token).read())
            self.assertEqual(state["runtime"]["workspace_path"], str(workspace))
            self.assertTrue(state["runtime"]["project_guidance"])
            listed = json.loads(self.request("/api/artifacts", token=self.ui.token).read())["artifacts"]
            self.assertEqual([(item["name"], item["path"]) for item in listed],
                             [("result.txt", "result.txt")])
            response = self.request(f"/api/artifacts/{listed[0]['index']}", token=self.ui.token)
            self.assertEqual(response.read(), b"from active workspace")

    def test_model_state_does_not_expose_credentials(self):
        with (patch("niji.webui.load_config", return_value={"api_keys": {"test": "private-test-secret"}}),
              patch("niji.webui.provider_names", return_value=["test", "openai"]),
              patch("niji.webui.provider_is_configured", side_effect=lambda name, cfg=None: name == "test")):
            state = json.loads(self.request("/api/models", token=self.ui.token).read())
        self.assertEqual(state["provider"], "test")
        self.assertEqual(state["model"], "demo-model")
        self.assertTrue(next(p for p in state["providers"] if p["name"] == "test")["configured"])
        self.assertNotIn("private-test-secret", json.dumps(state))

    def test_model_catalog_returns_models_for_configured_provider(self):
        with (patch("niji.webui.load_config", return_value={}),
              patch("niji.webui.provider_names", return_value=["demo"]),
              patch("niji.webui.provider_is_configured", return_value=True),
              patch("niji.webui.resolve_catalog_provider", return_value=({"provider": "demo"}, None)),
              patch("niji.webui.fetch_provider_models", return_value=(["demo-fast", "demo-pro"], ""))):
            result = json.loads(self.request("/api/models", {
                "action": "catalog", "provider": "demo"
            }, self.ui.token).read())
        self.assertEqual(result["models"], ["demo-fast", "demo-pro"])

    def test_model_switch_requires_successful_chat_test_before_persisting(self):
        cfg = {"api_key": "key-for-test", "base_url": "https://example.invalid/v1",
               "provider": "demo", "model": "demo-pro"}
        with (patch("niji.webui.load_config", return_value={}),
              patch("niji.webui.provider_names", return_value=["demo"]),
              patch("niji.webui.provider_is_configured", return_value=True),
              patch("niji.webui.resolve_provider", return_value=dict(cfg)),
              patch("niji.setup_wizard.test_connection", return_value=(True, "ok")),
              patch("niji.webui.save_config") as save,
              patch("openai.OpenAI", return_value=object())):
            result = json.loads(self.request("/api/models", {
                "action": "switch", "provider": "demo", "model": "demo-pro"
            }, self.ui.token).read())
        self.assertTrue(result["ok"])
        self.assertEqual(self.agent.provider_name, "demo")
        self.assertEqual(self.agent.model, "demo-pro")
        save.assert_called_once()
        self.assertFalse(self.ui._model_mutating)

    def test_failed_model_chat_test_does_not_change_active_model_or_config(self):
        with (patch("niji.webui.load_config", return_value={}),
              patch("niji.webui.provider_names", return_value=["demo"]),
              patch("niji.webui.provider_is_configured", return_value=True),
              patch("niji.webui.resolve_provider", return_value={"api_key": "secret", "base_url": "https://example.invalid/v1", "model": "bad"}),
              patch("niji.setup_wizard.test_connection", return_value=(False, "secret response")),
              patch("niji.webui.save_config") as save):
            with self.assertRaises(urllib.error.HTTPError) as failed:
                self.request("/api/models", {"action": "switch", "provider": "demo", "model": "bad"}, self.ui.token)
        self.assertEqual(failed.exception.code, 400)
        self.assertEqual(self.agent.model, "demo-model")
        save.assert_not_called()
        self.assertFalse(self.ui._model_mutating)

    def test_pinned_threads_persist_and_appear_in_session_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions, config = root / "sessions", root / "config"
            sessions.mkdir(); config.mkdir()
            (sessions / "thread-1.json").write_text(json.dumps([
                {"role": "user", "content": "Review this release"},
                {"role": "assistant", "content": "I will review it."},
            ]))
            with (patch("niji.webui.SESSION_DIR", sessions),
                  patch("niji.webui.CONFIG_DIR", config),
                  patch("niji.webui._PIN_FILE", config / "pinned_sessions.json")):
                result = json.loads(self.request("/api/sessions/thread-1/pin", {
                    "pinned": True
                }, self.ui.token).read())
                self.assertTrue(result["sessions"][0]["pinned"])
                self.assertEqual(json.loads((config / "pinned_sessions.json").read_text()), ["thread-1"])
                self.assertEqual((config / "pinned_sessions.json").stat().st_mode & 0o777, 0o600)
                result = json.loads(self.request("/api/sessions/thread-1/pin", {
                    "pinned": False
                }, self.ui.token).read())
                self.assertFalse(result["sessions"][0]["pinned"])

    def test_pin_endpoint_rejects_unsaved_threads_and_bad_values(self):
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.request("/api/sessions/not-saved/pin", {"pinned": True}, self.ui.token)
        self.assertEqual(missing.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request("/api/sessions/bad/pin", {"pinned": "yes"}, self.ui.token)
        self.assertEqual(invalid.exception.code, 400)

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

    def test_job_execution_timeline_is_request_scoped_and_redacts_secrets(self):
        started = json.loads(self.request("/api/chat", {"message": "show progress"}, self.ui.token).read())
        deadline = time.time() + 3
        job = None
        while time.time() < deadline:
            job = json.loads(self.request("/api/jobs/" + started["id"], token=self.ui.token).read())
            if job["status"] != "running":
                break
            time.sleep(0.03)
        self.assertTrue(job["events"])
        self.assertEqual(job["events"][0]["level"], "THINKING")
        self.assertEqual(job["events"][0]["message"], "Thinking through the next step")
        self.assertNotIn("private-test-secret", json.dumps(job["events"]))
        self.assertNotIn("demo-model", json.dumps(job["events"]))

    def test_settings_persist_automatic_compaction_preferences(self):
        with (patch("niji.webui.load_config", return_value={}),
              patch("niji.webui.save_config") as save):
            state = json.loads(self.request("/api/settings", {
                "auto_compact": False, "compaction_threshold": 40000
            }, self.ui.token).read())
        self.assertFalse(self.agent.auto_compact)
        self.assertEqual(self.agent.compaction_threshold, 40000)
        self.assertFalse(state["auto_compact"])
        self.assertEqual(state["compaction_threshold"], 40000)
        saved = save.call_args.args[0]
        self.assertEqual(saved, {"auto_compact": False, "compaction_threshold": 40000})
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.request("/api/settings", {"compaction_threshold": 2000}, self.ui.token).read()
        self.assertEqual(invalid.exception.code, 400)

    def test_live_tool_activity_and_elapsed_time_are_exposed_to_ui(self):
        job_id = "live-progress-case"
        self.ui._jobs[job_id] = {
            "id": job_id, "status": "running", "response": "", "error": "",
            "streamed": "", "progress": "Preparing the next step",
            "progress_detail": "Preparing the model request", "activity": None,
            "plan_only": False, "original_message": "run tests",
            "cancel_requested": False,
        }
        self.ui._active_job = job_id
        self.ui._busy = True
        self.ui._record_activity({
            "time": "12:03:00", "level": "TOOL",
            "message": "Tool call: run_tests · Running project tests",
        })
        started = json.loads(self.request(
            "/api/jobs/" + job_id, token=self.ui.token).read())
        self.assertEqual(started["progress"], "Using a tool")
        self.assertIn("Tool call: run_tests", started["progress_detail"])
        self.ui._record_activity({
            "time": "12:03:09", "level": "TOOL_PROGRESS",
            "message": "Tool call: run_tests · still running (9s)",
        })
        progress = json.loads(self.request(
            "/api/jobs/" + job_id, token=self.ui.token).read())
        self.assertEqual(progress["activity"]["level"], "TOOL_PROGRESS")
        self.assertIn("still running (9s)", progress["progress_detail"])
        self.ui._active_job = None
        self.ui._busy = False

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

    def test_settings_can_add_and_remove_nango_without_exposing_credentials(self):
        saved = {}

        class MockHttpMCP:
            def __init__(self, name, cfg):
                self.name, self.cfg = name, cfg
                self.tools = [{"name": "issue_search"}]
                self.stopped = False
            def start(self, timeout=20):
                self.timeout = timeout
            def stop(self):
                self.stopped = True

        def save(servers):
            saved.clear()
            saved.update(servers)

        with (patch("niji.webui.load_mcp_servers", side_effect=lambda: dict(saved)),
              patch("niji.webui.save_mcp_servers", side_effect=save),
              patch("niji.mcp.HttpMCPServer", MockHttpMCP)):
            result = json.loads(self.request("/api/connectors", {
                "action": "add_nango", "name": "nango_github",
                "api_key": "nango-private-api-key", "provider_config_key": "github-prod",
                "connection_id": "connection-private-id",
            }, self.ui.token).read())
            self.assertTrue(result["ok"])
            self.assertEqual(result["connectors"][0]["tools"], 1)
            self.assertTrue(result["connectors"][0]["connected"])
            self.assertNotIn("nango-private-api-key", json.dumps(result))
            self.assertNotIn("connection-private-id", json.dumps(result))
            state = json.loads(self.request("/api/state", token=self.ui.token).read())
            self.assertNotIn("nango-private-api-key", json.dumps(state))
            removed = json.loads(self.request("/api/connectors", {
                "action": "remove", "name": "nango_github"
            }, self.ui.token).read())
        self.assertTrue(removed["ok"])
        self.assertEqual(saved, {})
        self.assertEqual(self.agent.mcp_clients, [])

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
        self.assertEqual([item["content"] for item in job["plan"]],
                         ["Inspect the project", "Run the tests"])
        self.assertIn("plan", json.loads(self.request("/api/state", token=self.ui.token).read()))
        self.assertIn("Inspect the project", job["response"])

    def _wait_for_job(self, job_id, timeout=3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = json.loads(self.request("/api/jobs/" + job_id, token=self.ui.token).read())
            if job["status"] != "running":
                return job
            time.sleep(0.03)
        self.fail("job did not finish before timeout")

    def test_plan_preview_can_be_edited_then_approved_exactly(self):
        preview = json.loads(self.request("/api/chat", {
            "message": "inspect project", "plan_only": True,
        }, self.ui.token).read())
        plan_job = self._wait_for_job(preview["id"])
        original = list(plan_job["plan"])
        steps = [
            {"id": "inspect", "content": "Inspect the source tree",
             "acceptance_criteria": "Only the active project tree is examined.", "depends_on": []}, 
            {"id": "tests", "content": "Run the full test suite", "depends_on": ["inspect"]},
            {"id": "review", "content": "Review the diff", "depends_on": ["tests"]},
        ]
        with patch("niji.webui.load_plan", return_value=original):
            edited = json.loads(self.request(
                f"/api/jobs/{preview['id']}/edit-plan", {"steps": steps}, self.ui.token).read())
        self.assertTrue(edited["ok"])
        self.assertEqual([item["content"] for item in edited["plan"]],
                         [item["content"] for item in steps])
        self.assertEqual(edited["plan"][0]["acceptance_criteria"],
                         "Only the active project tree is examined.")
        self.assertEqual(edited["plan"][1]["depends_on"], ["inspect"])
        self.assertEqual(edited["plan"][2]["depends_on"], ["tests"])
        self.assertTrue(all(item["status"] == "pending" for item in edited["plan"]))
        source = json.loads(self.request(f"/api/jobs/{preview['id']}", token=self.ui.token).read())
        self.assertEqual(source["plan"], edited["plan"])
        with patch("niji.webui.load_plan", return_value=edited["plan"]):
            submitted = json.loads(self.request(
                f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token).read())
        execution = self._wait_for_job(submitted["id"])
        self.assertEqual(execution["status"], "completed")
        self.assertIn('"approved_steps":["Inspect the source tree","Run the full test suite","Review the diff"]',
                      execution["response"])
        self.assertIn('"id":"tests","content":"Run the full test suite","acceptance_criteria":"","depends_on":["inspect"]',
                      execution["response"])
        with patch("niji.webui.load_plan", return_value=edited["plan"]):
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.request(f"/api/jobs/{preview['id']}/edit-plan", {"steps": ["late edit"]}, self.ui.token)
        self.assertEqual(rejected.exception.code, 409)

    def test_plan_edits_persist_on_disk_and_are_the_plan_that_runs(self):
        from niji.planning import load_plan as persisted_load
        with tempfile.TemporaryDirectory() as tmp:
            with patch("niji.planning.save_plan",
                       side_effect=lambda sid, items: REAL_SAVE_PLAN(sid, items, root=tmp)), \
                 patch("niji.webui.load_plan",
                       side_effect=lambda sid: persisted_load(sid, root=tmp)), \
                 patch("niji.webui.save_plan",
                       side_effect=lambda sid, items: REAL_SAVE_PLAN(sid, items, root=tmp)):
                preview = json.loads(self.request("/api/chat", {
                    "message": "inspect project", "plan_only": True,
                }, self.ui.token).read())
                plan_job = self._wait_for_job(preview["id"])
                self.assertTrue(plan_job["plan"])
                edited = json.loads(self.request(
                    f"/api/jobs/{preview['id']}/edit-plan",
                    {"steps": ["Inspect only the active workspace", "Run its tests"]},
                    self.ui.token).read())
                self.assertEqual(persisted_load("test-session", root=tmp), edited["plan"])
                submitted = json.loads(self.request(
                    f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token).read())
                execution = self._wait_for_job(submitted["id"])
                self.assertIn('"approved_steps":["Inspect only the active workspace","Run its tests"]',
                              execution["response"])

    def test_empty_new_preview_clears_stale_steps_and_can_be_repaired_manually(self):
        self.agent.todos = {"items": [{"content": "steps from an older request", "status": "pending"}]}
        with patch("niji.webui.extract_plan_steps", return_value=[]):
            preview = json.loads(self.request("/api/chat", {
                "message": "new request", "plan_only": True,
            }, self.ui.token).read())
            plan_job = self._wait_for_job(preview["id"])
        self.assertEqual(plan_job["plan"], [])
        self.assertEqual(self.agent.todos["items"], [])
        with patch("niji.webui.load_plan", return_value=[]):
            with self.assertRaises(urllib.error.HTTPError) as empty_approval:
                self.request(f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token)
        self.assertEqual(empty_approval.exception.code, 409)
        with patch("niji.webui.load_plan", return_value=[]):
            edited = json.loads(self.request(f"/api/jobs/{preview['id']}/edit-plan",
                                             {"steps": ["Inspect the requested scope"]},
                                             self.ui.token).read())
        self.assertEqual(edited["plan"][0]["content"], "Inspect the requested scope")

    def test_plan_edit_rejects_malformed_empty_oversized_and_stale_updates(self):
        preview = json.loads(self.request("/api/chat", {
            "message": "inspect project", "plan_only": True,
        }, self.ui.token).read())
        plan_job = self._wait_for_job(preview["id"])
        path = f"/api/jobs/{preview['id']}/edit-plan"
        for payload in ({"steps": []}, {"steps": ["ok", 7]},
                        {"steps": ["step"] * 61},
                        {"steps": ["ok"], "status": "completed"}):
            with self.assertRaises(urllib.error.HTTPError) as bad:
                self.request(path, payload, self.ui.token)
            self.assertEqual(bad.exception.code, 400)
        invalid_graphs = [
            [{"id": "a", "content": "A", "depends_on": ["missing"]}],
            [{"id": "a", "content": "A", "depends_on": ["a"]}],
            [{"id": "a", "content": "A", "depends_on": ["b"]},
             {"id": "b", "content": "B", "depends_on": ["a"]}],
            [{"id": "first", "content": "First", "depends_on": ["later"]},
             {"id": "later", "content": "Later"}],
            [{"id": "a", "content": "A", "status": "completed"}],
        ]
        with patch("niji.webui.load_plan", return_value=plan_job["plan"]):
            for graph in invalid_graphs:
                with self.subTest(graph=graph), self.assertRaises(urllib.error.HTTPError) as invalid:
                    self.request(path, {"steps": graph}, self.ui.token)
                self.assertEqual(invalid.exception.code, 400)
        with patch("niji.webui.load_plan", return_value=[{"content": "stale plan"}]):
            with self.assertRaises(urllib.error.HTTPError) as stale:
                self.request(path, {"steps": ["replace stale plan"]}, self.ui.token)
        self.assertEqual(stale.exception.code, 409)
        with patch("niji.webui.load_plan", return_value=plan_job["plan"]):
            self.agent.session_id = "different-thread"
            with self.assertRaises(urllib.error.HTTPError) as wrong_thread:
                self.request(path, {"steps": ["cross-thread edit"]}, self.ui.token)
        self.assertEqual(wrong_thread.exception.code, 409)
        self.agent.session_id = "test-session"
        with patch("niji.webui.load_plan", return_value=plan_job["plan"]), \
             patch("niji.webui.save_plan", side_effect=OSError("disk full")):
            with self.assertRaises(urllib.error.HTTPError) as storage_error:
                self.request(path, {"steps": ["would not persist"]}, self.ui.token)
        self.assertEqual(storage_error.exception.code, 500)
        self.assertEqual(json.loads(self.request(
            f"/api/jobs/{preview['id']}", token=self.ui.token).read())["plan"], plan_job["plan"])

    def test_approved_plan_runs_from_unchanged_server_saved_plan_once(self):
        preview = json.loads(self.request("/api/chat", {
            "message": "inspect project", "plan_only": True,
        }, self.ui.token).read())
        plan_job = self._wait_for_job(preview["id"])
        self.assertEqual(plan_job["status"], "completed")
        saved_plan = list(plan_job["plan"])
        with patch("niji.webui.load_plan", return_value=saved_plan):
            submitted = json.loads(self.request(
                f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token).read())
        self.assertTrue(submitted["ok"])
        run_job = self._wait_for_job(submitted["id"])
        self.assertEqual(run_job["status"], "completed")
        self.assertFalse(run_job["plan_only"])
        self.assertEqual(self.agent.approved_plan_seen, saved_plan)
        self.assertIsNone(self.agent.approved_plan)
        self.assertEqual([item["status"] for item in run_job["plan"]],
                         ["completed", "completed"])
        self.assertIn('"original_request":"inspect project"', run_job["response"])
        self.assertIn('"approved_steps":["Inspect the project","Run the tests"]', run_job["response"])
        updated_source = json.loads(self.request(
            f"/api/jobs/{preview['id']}", token=self.ui.token).read())
        self.assertTrue(updated_source["plan_approved"])
        with patch("niji.webui.load_plan", return_value=saved_plan):
            with self.assertRaises(urllib.error.HTTPError) as duplicate:
                self.request(f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token)
        self.assertEqual(duplicate.exception.code, 409)

    def test_final_success_guard_revalidates_evidence_after_in_memory_mutation(self):
        preview = json.loads(self.request("/api/chat", {
            "message": "inspect project", "plan_only": True,
        }, self.ui.token).read())
        plan_job = self._wait_for_job(preview["id"])
        self.agent.corrupt_completion_evidence = True
        with patch("niji.webui.load_plan", return_value=plan_job["plan"]):
            submitted = json.loads(self.request(
                f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token).read())
        result = self._wait_for_job(submitted["id"])
        self.assertEqual(result["status"], "error")
        self.assertIn("invalid completion evidence", result["error"])
        self.agent.corrupt_completion_evidence = False

    def test_approved_plan_cannot_report_success_when_steps_remain_incomplete(self):
        preview = json.loads(self.request("/api/chat", {
            "message": "inspect project", "plan_only": True,
        }, self.ui.token).read())
        plan_job = self._wait_for_job(preview["id"])
        self.agent.leave_plan_incomplete = True
        with patch("niji.webui.load_plan", return_value=plan_job["plan"]):
            submitted = json.loads(self.request(
                f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token).read())
        result = self._wait_for_job(submitted["id"])
        self.assertEqual(result["status"], "error")
        self.assertIn("unfinished steps", result["error"])

    def test_plan_approval_rejects_stale_saved_plan_and_wrong_thread(self):
        preview = json.loads(self.request("/api/chat", {
            "message": "inspect project", "plan_only": True,
        }, self.ui.token).read())
        plan_job = self._wait_for_job(preview["id"])
        with patch("niji.webui.load_plan", return_value=[{"content": "changed after preview"}]):
            with self.assertRaises(urllib.error.HTTPError) as stale:
                self.request(f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token)
        self.assertEqual(stale.exception.code, 409)
        self.assertFalse(json.loads(self.request(
            f"/api/jobs/{preview['id']}", token=self.ui.token).read())["plan_approved"])
        saved_plan = list(plan_job["plan"])
        self.agent.session_id = "another-thread"
        with patch("niji.webui.load_plan", return_value=saved_plan):
            with self.assertRaises(urllib.error.HTTPError) as wrong_thread:
                self.request(f"/api/jobs/{preview['id']}/approve-plan", {}, self.ui.token)
        self.assertEqual(wrong_thread.exception.code, 409)

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
