"""Minimal MCP (Model Context Protocol) stdio client — connector support.

Connects to any MCP server (GitHub, Postgres, filesystem, Slack, ...).
Config lives in ~/.niji/mcp.json:
    {"servers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}}}
"""
import json
import queue
import subprocess
import threading

from .safety import subprocess_environment


class MCPServer:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.command = cfg["command"]
        self.args = cfg.get("args", [])
        self.env = cfg.get("env", {})
        self.proc = None
        self.tools = []
        self._id = 0
        self._pending = {}
        self._send_lock = threading.Lock()

    # ---------- lifecycle ----------

    def start(self, timeout=20):
        # Credentials needed by a connector belong in its explicit mcp.json env.
        env = subprocess_environment(self.env)
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
        )
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "niji-agent", "version": "2.0.0"},
        }, timeout=timeout)
        self._notify("notifications/initialized", {})
        try:
            self.tools = self._request("tools/list", {}, timeout=timeout).get("tools", [])
        except Exception:
            self.tools = []

    def stop(self):
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass

    # ---------- wire protocol (newline-delimited JSON-RPC) ----------

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            mid = msg.get("id")
            if mid is not None and mid in self._pending:
                self._pending[mid].put(msg)

    def _request(self, method, params, timeout=60):
        with self._send_lock:
            self._id += 1
            mid = self._id
            q = queue.Queue()
            self._pending[mid] = q
            self.proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
            self.proc.stdin.flush()
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            del self._pending[mid]
            raise TimeoutError(f"MCP server '{self.name}' did not respond to {method}")
        finally:
            self._pending.pop(mid, None)
        if "error" in resp:
            raise RuntimeError(f"MCP error from '{self.name}': {resp['error']}")
        return resp.get("result", {})

    def _notify(self, method, params):
        with self._send_lock:
            self.proc.stdin.write(json.dumps({
                "jsonrpc": "2.0", "method": method, "params": params}) + "\n")
            self.proc.stdin.flush()

    # ---------- tool bridge ----------

    def to_openai_tools(self):
        out = []
        for t in self.tools:
            out.append({
                "type": "function",
                "function": {
                    "name": f"{self.name}__{t['name']}",
                    "description": (t.get("description") or "")[:1000],
                    "parameters": t.get("inputSchema") or {
                        "type": "object", "properties": {}},
                },
            })
        return out

    def call(self, tool_name, args):
        result = self._request("tools/call",
                               {"name": tool_name, "arguments": args}, timeout=180)
        parts = []
        for c in result.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(json.dumps(c)[:2000])
        return "\n".join(parts) or json.dumps(result)[:2000]


def connect_all(servers_cfg: dict):
    """Start every configured MCP server; a failing server never kills the agent."""
    clients = []
    for name, cfg in (servers_cfg or {}).items():
        try:
            c = MCPServer(name, cfg)
            c.start()
            print(f"[niji] connector connected: {name} ({len(c.tools)} tools)")
            clients.append(c)
        except Exception as e:
            print(f"[niji] connector '{name}' failed to start: {e}")
    return clients
