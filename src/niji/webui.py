"""Token-protected, loopback-only browser UI for Niji Agent."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .compaction import estimate_tokens
from .terminal import safe_terminal_text

_MAX_BODY = 32_000
_MAX_PROMPT = 20_000


_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Niji Agent · Local UI</title>
<style>
:root{color-scheme:dark;--bg:#090c15;--panel:#111827;--panel2:#151e30;--line:#26334b;--text:#edf3ff;--muted:#93a3bc;--cyan:#46d7e8;--violet:#a88bff;--green:#68e1a2;--amber:#ffca73;--red:#ff7b8c}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 10% 0%,#15223b 0,transparent 44%),var(--bg);color:var(--text);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;min-height:100vh}
.shell{max-width:1440px;margin:auto;padding:22px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:14px;background:linear-gradient(145deg,var(--cyan),var(--violet));display:grid;place-items:center;color:#07111b;font-size:23px;font-weight:900;box-shadow:0 8px 30px #46d7e833}.brand h1{font-size:20px;margin:0;letter-spacing:.08em}.brand small{display:block;color:var(--muted);font-size:12px}.chips{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.chip{padding:7px 11px;border:1px solid var(--line);border-radius:999px;background:#0e1625;color:#c5d3e8;font-size:12px}.chip strong{color:var(--cyan);font-weight:650}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:16px;align-items:stretch}.card{background:linear-gradient(155deg,#121b2aee,#0f1624ee);border:1px solid var(--line);border-radius:18px;box-shadow:0 14px 40px #0004}.chat{min-height:calc(100vh - 126px);display:flex;flex-direction:column;overflow:hidden}.chathead{padding:15px 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}.chathead h2,.section h3{margin:0;font-size:13px;letter-spacing:.11em;text-transform:uppercase;color:#dbe7f8}.status{color:var(--green);font-size:12px}.messages{flex:1;overflow:auto;padding:20px;display:flex;flex-direction:column;gap:14px;min-height:280px}.bubble{max-width:min(88%,850px);padding:13px 15px;border:1px solid var(--line);border-radius:14px;background:#141e2e;white-space:pre-wrap;overflow-wrap:anywhere}.bubble.user{align-self:flex-end;background:#152a3b;border-color:#246176}.bubble.assistant{align-self:flex-start}.bubble .label{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--cyan);margin-bottom:6px}.bubble.user .label{color:var(--violet)}.welcome{margin:auto;text-align:center;color:var(--muted);max-width:480px;padding:25px}.welcome b{display:block;font-size:24px;color:var(--text);margin-bottom:7px}.composer{padding:14px 16px 16px;border-top:1px solid var(--line);background:#0c1320}.composer form{display:flex;gap:10px;align-items:flex-end}.composer textarea{resize:vertical;min-height:52px;max-height:180px;flex:1;border:1px solid #34445f;border-radius:12px;background:#111b2a;color:var(--text);font:inherit;padding:13px;outline:none}.composer textarea:focus{border-color:var(--cyan);box-shadow:0 0 0 3px #46d7e81c}.button{border:0;border-radius:11px;padding:12px 17px;font:inherit;font-weight:700;cursor:pointer;color:#07111b;background:linear-gradient(120deg,var(--cyan),#8ce5cc)}.button:disabled{opacity:.5;cursor:wait}.hint{margin-top:8px;color:#73839d;font-size:11px}.side{display:flex;flex-direction:column;gap:14px}.section{padding:16px}.section h3{margin-bottom:13px}.metric{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid #ffffff0d;color:var(--muted);font-size:12px}.metric:last-child{border:0}.metric b{color:#e6efff;font-weight:550;text-align:right;overflow-wrap:anywhere}.tools{display:flex;gap:6px;flex-wrap:wrap;max-height:240px;overflow:auto}.tool{font:11px ui-monospace,SFMono-Regular,monospace;color:#c6d3e9;background:#162237;border:1px solid #293a55;border-radius:7px;padding:4px 7px}.activity{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:9px;max-height:220px;overflow:auto}.activity li{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.activity time{color:#657791;margin-right:6px}.approval{display:none;margin:0 0 12px;padding:14px;border:1px solid #9a7330;border-radius:12px;background:#292215}.approval.show{display:block}.approval strong{color:var(--amber)}.approval pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:160px;overflow:auto;color:#dfd1b0;font-size:11px}.approval button{margin-right:7px}.deny{background:#5a2b39;color:#ffe5ea}.foot{color:#6f809a;font-size:11px;text-align:center;margin-top:15px}
@media(max-width:900px){.shell{padding:12px}.layout{grid-template-columns:1fr}.chat{min-height:68vh}.side{display:grid;grid-template-columns:1fr 1fr}.side .section:last-child{grid-column:1/-1}.top{align-items:flex-start;flex-direction:column}.chips{justify-content:flex-start}}
@media(max-width:560px){.side{grid-template-columns:1fr}.side .section:last-child{grid-column:auto}.messages{padding:14px}.bubble{max-width:96%}.composer form{align-items:stretch;flex-direction:column}.button{align-self:flex-end}.chat{min-height:72vh}}
</style></head><body><div class="shell">
<header class="top"><div class="brand"><div class="mark">✧</div><div><h1>NIJI AGENT</h1><small>Your ideas, in motion · local browser workspace</small></div></div><div class="chips"><span class="chip" id="provider">Provider · —</span><span class="chip" id="model">Model · —</span><span class="chip" id="runtime">Local session · —</span></div></header>
<div class="layout"><section class="card chat"><div class="chathead"><h2>✧ Chat</h2><span class="status" id="connection">● Connecting</span></div><div class="messages" id="messages"><div class="welcome"><b>Ready when you are.</b>Ask Niji to research, work with files, run tests, or help with a project.</div></div><div class="composer"><div class="approval" id="approval"><strong id="approval-title">Approval needed</strong><pre id="approval-preview"></pre><button class="button" id="approve">Approve once</button><button class="button deny" id="deny">Deny</button></div><form id="form"><textarea id="prompt" maxlength="20000" placeholder="Ask Niji anything…" aria-label="Message"></textarea><button class="button" id="send">Send&nbsp; ↗</button></form><div class="hint">Enter to send · Shift+Enter for a new line · Tool actions ask before running</div></div></section>
<aside class="side"><section class="card section"><h3>Session overview</h3><div class="metric"><span>Version</span><b id="version">—</b></div><div class="metric"><span>Turns</span><b id="turns">—</b></div><div class="metric"><span>Tool calls</span><b id="toolcalls">—</b></div><div class="metric"><span>Context est.</span><b id="context">—</b></div><div class="metric"><span>Approval mode</span><b id="approvalmode">—</b></div></section><section class="card section"><h3>Available tools</h3><div class="tools" id="tools"></div></section><section class="card section"><h3>Recent activity</h3><ul class="activity" id="activity"></ul></section></aside></div><div class="foot">Bound to this device only · Keep the private URL to yourself · Niji local UI</div></div>
<script>
const token=new URLSearchParams(location.search).get('token');let activeJob=null,pendingApproval=null,lastMessageCount=0;
const el=id=>document.getElementById(id);
function bubble(role,text){const box=document.createElement('div');box.className='bubble '+role;const label=document.createElement('div');label.className='label';label.textContent=role==='user'?'You':'Niji';const body=document.createElement('div');body.textContent=text||'';box.append(label,body);el('messages').append(box);el('messages').scrollTop=el('messages').scrollHeight;return box}
async function api(path,options={}){const headers={...(options.headers||{}),'X-Niji-Token':token};if(options.body)headers['Content-Type']='application/json';const r=await fetch(path,{...options,headers,cache:'no-store'});if(!r.ok)throw new Error((await r.text()).slice(0,300)||('HTTP '+r.status));return r.json()}
function setStatus(text,ok=true){el('connection').textContent=(ok?'● ':'● ')+text;el('connection').style.color=ok?'var(--green)':'var(--red)'}
function renderState(s){el('provider').innerHTML='Provider · <strong></strong>';el('provider').querySelector('strong').textContent=s.provider||'—';el('model').innerHTML='Model · <strong></strong>';el('model').querySelector('strong').textContent=s.model||'—';el('runtime').textContent='Local session · '+s.session_id;el('version').textContent=s.version;el('turns').textContent=s.usage.turns;el('toolcalls').textContent=s.tool_calls+'/'+s.limits.max_tool_calls;el('context').textContent='~'+Number(s.context_tokens||0).toLocaleString()+' tokens';el('approvalmode').textContent=s.approval;
const tools=el('tools');tools.replaceChildren();for(const name of s.tools){const tag=document.createElement('span');tag.className='tool';tag.textContent=name;tools.append(tag)}
const list=el('activity');list.replaceChildren();for(const item of s.activity.slice(-12).reverse()){const li=document.createElement('li'),time=document.createElement('time');time.textContent=item.time||'';li.append(time,document.createTextNode(item.message||''));list.append(li)}
if(s.pending_approvals.length){const a=s.pending_approvals[0];pendingApproval=a.id;el('approval').classList.add('show');el('approval-title').textContent='Approve '+a.tool+'?';el('approval-preview').textContent=a.preview}else{pendingApproval=null;el('approval').classList.remove('show')}
setStatus(s.busy?'Niji is working…':'Connected');el('send').disabled=!!s.busy;el('prompt').disabled=!!s.busy;
}
async function refresh(){try{const s=await api('/api/state');renderState(s)}catch(e){setStatus('Disconnected · '+e.message,false)}}
async function initial(){if(!token){setStatus('Missing private link token',false);return}try{const s=await api('/api/state');renderState(s);el('messages').replaceChildren();for(const m of s.transcript){if(m.content)bubble(m.role==='user'?'user':'assistant',m.content)}if(!s.transcript.length){const w=document.createElement('div');w.className='welcome';w.innerHTML='<b>Ready when you are.</b>Ask Niji to research, work with files, run tests, or help with a project.';el('messages').append(w)}}catch(e){setStatus('Could not connect · '+e.message,false)}}
async function send(){const input=el('prompt'),text=input.value.trim();if(!text||activeJob)return;const welcome=document.querySelector('.welcome');if(welcome)welcome.remove();bubble('user',text);input.value='';el('send').disabled=true;el('prompt').disabled=true;try{const r=await api('/api/chat',{method:'POST',body:JSON.stringify({message:text})});activeJob=r.id;const wait=bubble('assistant','Niji is working…');wait.id='working';while(activeJob){await new Promise(resolve=>setTimeout(resolve,750));await refresh();try{const job=await api('/api/jobs/'+activeJob);if(job.status==='completed'){document.getElementById('working')?.remove();bubble('assistant',job.response||'(No text response)');activeJob=null}else if(job.status==='error'){document.getElementById('working')?.remove();bubble('assistant','Request failed: '+job.error);activeJob=null}}catch(e){document.getElementById('working')?.remove();bubble('assistant','Could not read job status: '+e.message);activeJob=null}}}catch(e){bubble('assistant','Could not start request: '+e.message)}finally{el('send').disabled=false;el('prompt').disabled=false;el('prompt').focus();refresh()}}
el('form').addEventListener('submit',e=>{e.preventDefault();send()});el('prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();el('form').requestSubmit()}});
async function decide(approved){if(!pendingApproval)return;const id=pendingApproval;el('approve').disabled=true;el('deny').disabled=true;try{await api('/api/approvals/'+id,{method:'POST',body:JSON.stringify({approved})});await refresh()}catch(e){setStatus('Approval failed: '+e.message,false)}finally{el('approve').disabled=false;el('deny').disabled=false}}
el('approve').onclick=()=>decide(true);el('deny').onclick=()=>decide(false);initial();setInterval(refresh,1800);
</script></body></html>'''


class NijiWebUI:
    def __init__(self, agent, host: str = "127.0.0.1", port: int = 8765):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Niji Web UI only binds to localhost/loopback addresses")
        if not 0 <= int(port) <= 65535:
            raise ValueError("port must be between 0 and 65535")
        self.agent = agent
        self.host = host
        self.port = int(port)
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._busy = False
        self._active_job = None
        self._jobs = {}
        self._approvals = {}
        self._previous_activity_callback = getattr(agent, "activity_callback", None)
        self._previous_approval_callback = getattr(agent, "approval_callback", None)
        agent.activity_callback = self._record_activity
        agent.approval = "ask" if getattr(agent, "approval", "ask") != "auto" else "auto"
        agent.approval_callback = self._request_approval
        self.httpd = self._create_server()

    def _create_server(self):
        ui = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "NijiLocal/1"
            sys_version = ""

            def log_message(self, fmt, *args):
                # Do not log request paths; the one-time bearer token is in the UI URL.
                return

            def _send(self, status, body, content_type="application/json; charset=utf-8"):
                payload = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, status, data):
                self._send(status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

            def _host_ok(self):
                try:
                    host_header = self.headers.get("Host", "")
                    parsed = urlsplit("//" + host_header)
                    return (parsed.hostname in ("127.0.0.1", "localhost", "::1")
                            and parsed.port == ui.httpd.server_port)
                except Exception:
                    return False

            def _origin_ok(self):
                origin = self.headers.get("Origin")
                if not origin:
                    return True
                try:
                    parsed = urlsplit(origin)
                    return (parsed.scheme == "http"
                            and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                            and parsed.port == ui.httpd.server_port)
                except Exception:
                    return False

            def _authorized(self):
                return (self._host_ok() and self._origin_ok()
                        and hmac.compare_digest(self.headers.get("X-Niji-Token", ""), ui.token))

            def _read_json(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return None
                if size < 1 or size > _MAX_BODY:
                    return None
                try:
                    return json.loads(self.rfile.read(size))
                except Exception:
                    return None

            def do_GET(self):
                parsed = urlsplit(self.path)
                if not self._host_ok():
                    self._json(403, {"error": "Host is not allowed"}); return
                if parsed.path == "/":
                    supplied = parse_qs(parsed.query).get("token", [""])[0]
                    if not hmac.compare_digest(supplied, ui.token):
                        self._json(403, {"error": "Use the private URL printed by Niji"}); return
                    self._send(200, _PAGE, "text/html; charset=utf-8"); return
                if not self._authorized():
                    self._json(403, {"error": "Not authorized"}); return
                if parsed.path == "/api/state":
                    self._json(200, ui._state()); return
                if parsed.path.startswith("/api/jobs/"):
                    job_id = parsed.path.rsplit("/", 1)[-1]
                    with ui._lock:
                        job = ui._jobs.get(job_id)
                        if job:
                            result = {k: job.get(k) for k in ("id", "status", "response", "error")}
                        else:
                            result = None
                    if result is None:
                        self._json(404, {"error": "Unknown job"})
                    else:
                        self._json(200, result)
                    return
                self._json(404, {"error": "Not found"})

            def do_POST(self):
                if not self._host_ok() or not self._origin_ok():
                    self._json(403, {"error": "Host/origin is not allowed"}); return
                if not self._authorized():
                    self._json(403, {"error": "Not authorized"}); return
                parsed = urlsplit(self.path)
                data = self._read_json()
                if data is None:
                    self._json(400, {"error": "Invalid or oversized JSON body"}); return
                if parsed.path == "/api/chat":
                    message = data.get("message") if isinstance(data, dict) else None
                    if not isinstance(message, str) or not message.strip() or len(message) > _MAX_PROMPT:
                        self._json(400, {"error": f"Message must contain 1-{_MAX_PROMPT} characters"}); return
                    with ui._lock:
                        if ui._busy:
                            self._json(409, {"error": "Niji is already working on a request"}); return
                        job_id = uuid.uuid4().hex
                        ui._busy = True
                        ui._active_job = job_id
                        ui._jobs[job_id] = {"id": job_id, "status": "running", "response": "", "error": "", "created": time.time()}
                    threading.Thread(target=ui._run_job, args=(job_id, message.strip()), daemon=True).start()
                    self._json(202, {"id": job_id}); return
                if parsed.path.startswith("/api/approvals/"):
                    approval_id = parsed.path.rsplit("/", 1)[-1]
                    approved = data.get("approved") if isinstance(data, dict) else None
                    if not isinstance(approved, bool):
                        self._json(400, {"error": "approved must be true or false"}); return
                    with ui._lock:
                        approval = ui._approvals.get(approval_id)
                        if approval is None:
                            self._json(404, {"error": "Approval expired or unknown"}); return
                        approval["approved"] = approved
                        approval["event"].set()
                    self._json(200, {"ok": True}); return
                self._json(404, {"error": "Not found"})

        return ThreadingHTTPServer((self.host, self.port), Handler)

    @property
    def url(self):
        host = "[::1]" if self.host == "::1" else ("127.0.0.1" if self.host == "localhost" else self.host)
        return f"http://{host}:{self.httpd.server_port}/?token={self.token}"

    def _record_activity(self, event):
        callback = self._previous_activity_callback
        if callback:
            try:
                callback(event)
            except Exception:
                pass

    def _request_approval(self, tool_name, args):
        approval_id = uuid.uuid4().hex
        preview = json.dumps(args, ensure_ascii=False, default=str)[:1200]
        api_key = str(getattr(self.agent, "provider_cfg", {}).get("api_key", ""))
        if len(api_key) >= 6:
            preview = preview.replace(api_key, "[redacted]")
        event = threading.Event()
        item = {"id": approval_id, "tool": tool_name, "preview": safe_terminal_text(preview),
                "event": event, "approved": False}
        with self._lock:
            self._approvals[approval_id] = item
        decided = event.wait(180)
        with self._lock:
            self._approvals.pop(approval_id, None)
        return bool(decided and item["approved"])

    def _run_job(self, job_id, message):
        started = time.monotonic()
        try:
            response = self.agent.chat(message)
            output = str(response or "[done]")[:40_000]
            with self._lock:
                job = self._jobs[job_id]
                job.update(status="completed", response=output, error="")
        except Exception as exc:
            message = str(exc)
            api_key = str(getattr(self.agent, "provider_cfg", {}).get("api_key", ""))
            if len(api_key) >= 6:
                message = message.replace(api_key, "[redacted]")
            safe = safe_terminal_text(message)[:1500]
            with self._lock:
                job = self._jobs[job_id]
                job.update(status="error", response="", error=f"{exc.__class__.__name__}: {safe}")
        finally:
            try:
                self.agent.request_seconds = max(0, int(time.monotonic() - started))
            except Exception:
                pass
            with self._lock:
                self._busy = False
                if self._active_job == job_id:
                    self._active_job = None
                # Keep only a small recent job cache for this private UI session.
                if len(self._jobs) > 20:
                    for old_id in list(self._jobs)[:-20]:
                        self._jobs.pop(old_id, None)

    def _state(self):
        with self._lock:
            pending = [{"id": a["id"], "tool": a["tool"], "preview": a["preview"]}
                       for a in self._approvals.values()]
            busy, active = self._busy, self._active_job
        with getattr(self.agent, "_activity_lock", threading.RLock()):
            activity = list(getattr(self.agent, "activity", []))[-24:]
            usage = dict(getattr(self.agent, "usage", {}))
            tool_usage = dict(getattr(self.agent, "tool_usage", {}))
        provider_cfg = getattr(self.agent, "provider_cfg", {})
        messages = getattr(self.agent, "messages", [])
        transcript = []
        for msg in messages:
            content = msg.get("content")
            if (msg.get("role") in ("user", "assistant") and content
                    and not msg.get("tool_calls")
                    and not str(content).startswith("[Environment:")):
                transcript.append({"role": msg["role"], "content": str(content)[:10_000]})
        return {
            "version": __version__, "provider": getattr(self.agent, "provider_name", provider_cfg.get("provider", "unknown")),
            "model": getattr(self.agent, "model", provider_cfg.get("model", "unknown")),
            "session_id": getattr(self.agent, "session_id", "local"), "approval": getattr(self.agent, "approval", "ask"),
            "busy": busy, "active_job": active, "pending_approvals": pending,
            "tools": [s.get("function", {}).get("name", "tool") for s in getattr(self.agent, "tool_schemas", [])],
            "activity": activity, "usage": usage, "tool_usage": tool_usage,
            "tool_calls": sum(tool_usage.values()),
            "context_tokens": estimate_tokens(messages), "transcript": transcript[-80:],
            "limits": {"max_turns": getattr(self.agent, "max_turns", 20),
                       "max_tool_calls": getattr(self.agent, "max_tool_calls", 30),
                       "max_tool_calls_per_turn": getattr(self.agent, "max_tool_calls_per_turn", 6)},
        }

    def serve_forever(self, open_browser: bool = False):
        print(f"Niji local UI: {self.url}")
        print("Loopback only · tool actions ask for approval · Ctrl+C to stop")
        if open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass
        try:
            self.httpd.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            print("\nStopping Niji local UI…")
        finally:
            self.close()

    def close(self):
        """Stop the listener and deny any still-pending approval requests."""
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        self.httpd.server_close()
        with self._lock:
            for item in self._approvals.values():
                item["approved"] = False
                item["event"].set()
        try:
            self.agent.approval_callback = self._previous_approval_callback
            if self.agent.activity_callback == self._record_activity:
                self.agent.activity_callback = self._previous_activity_callback
        except Exception:
            pass
