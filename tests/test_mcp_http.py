import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from niji.cli import _cmd_connectors
from niji.config import save_mcp_servers
from niji.mcp import HttpMCPServer, connect_all


class FakeHTTPClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.closed = False

    def post(self, url, *, headers, json, timeout):
        self.calls.append((url, headers, json, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def delete(self, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("DELETE", "https://example.test"))

    def close(self):
        self.closed = True


def json_response(payload, *, headers=None):
    return httpx.Response(200, json=payload, headers=headers or {},
                          request=httpx.Request("POST", "https://example.test/mcp"))


class HttpMCPTests(unittest.TestCase):
    def _client(self, payloads, headers=None):
        return FakeHTTPClient([json_response(p, headers=headers if i == 0 else None)
                               for i, p in enumerate(payloads)])

    def test_nango_handshake_discovers_tools_and_uses_documented_auth_headers(self):
        transport = self._client([
            {"jsonrpc": "2.0", "id": 1,
             "result": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {},
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": [
                {"name": "search", "description": "Search items",
                 "inputSchema": {"type": "object", "properties": {}}}]}}],
            headers={"MCP-Session-Id": "session-abc"})
        config = {"transport": "http", "url": "https://api.nango.dev/proxy/v2/mcp",
                  "api_key": "nango-secret", "provider_config_key": "linear",
                  "connection_id": "conn-1"}
        with patch("niji.mcp.httpx.Client", return_value=transport):
            client = HttpMCPServer("nango_linear", config)
            client.start()
        self.assertEqual(len(client.tools), 1)
        init_url, headers, body, _ = transport.calls[0]
        self.assertEqual(init_url, config["url"])
        self.assertEqual(headers["Authorization"], "Bearer nango-secret")
        self.assertEqual(headers["Provider-Config-Key"], "linear")
        self.assertEqual(headers["Connection-Id"], "conn-1")
        self.assertEqual(body["method"], "initialize")
        self.assertEqual(transport.calls[1][1]["MCP-Session-Id"], "session-abc")
        self.assertEqual(transport.calls[2][1]["MCP-Protocol-Version"], "2024-11-05")
        self.assertEqual(client.to_openai_tools()[0]["function"]["name"],
                         "nango_linear__search")

    def test_tool_calls_return_text_and_propagate_args(self):
        transport = self._client([
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            {},
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}},
            {"jsonrpc": "2.0", "id": 4, "result": {"content": [
                {"type": "text", "text": "Found 2 records"}]}}])
        with patch("niji.mcp.httpx.Client", return_value=transport):
            client = HttpMCPServer("nango", {"url": "https://api.nango.dev/proxy/v2/mcp"})
            client.start()
            result = client.call("search", {"query": "test"})
        self.assertEqual(result, "Found 2 records")
        self.assertEqual(transport.calls[3][2]["params"], {
            "name": "search", "arguments": {"query": "test"}})

    def test_server_sent_event_responses_are_parsed(self):
        sse = httpx.Response(200, headers={"content-type": "text/event-stream"},
                             text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}\n\n',
                             request=httpx.Request("POST", "https://example.test/mcp"))
        transport = FakeHTTPClient([sse, json_response({}),
                                    json_response({"jsonrpc": "2.0", "id": 3,
                                                  "result": {"tools": []}})])
        with patch("niji.mcp.httpx.Client", return_value=transport):
            client = HttpMCPServer("remote", {"url": "https://example.test/mcp"})
            client.start()
        self.assertEqual(client.tools, [])

    def test_auth_failure_is_diagnostic_and_does_not_echo_secrets(self):
        error = httpx.Response(401, text="rejected", request=httpx.Request(
            "POST", "https://example.test/mcp"))
        transport = FakeHTTPClient([error])
        with patch("niji.mcp.httpx.Client", return_value=transport):
            client = HttpMCPServer("private", {"url": "https://example.test/mcp",
                                                "api_key": "super-secret"})
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                client.start()
        self.assertNotIn("super-secret", str(raised.exception))

    def test_env_references_resolve_without_saving_secret_in_config(self):
        transport = self._client([
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            {},
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}])
        with patch.dict(os.environ, {"NANGO_KEY_TEST": "env-secret"}), \
             patch("niji.mcp.httpx.Client", return_value=transport):
            client = HttpMCPServer("nango", {"url": "https://api.nango.dev/proxy/v2/mcp",
                                              "api_key": "${NANGO_KEY_TEST}"})
            client.start()
        self.assertEqual(transport.calls[0][1]["Authorization"], "Bearer env-secret")
        self.assertNotIn("env-secret", json.dumps({"api_key": "${NANGO_KEY_TEST}"}))

    def test_missing_env_reference_fails_without_printing_any_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "missing environment variable"):
                HttpMCPServer("nango", {"url": "https://example.test/mcp",
                                         "api_key": "${NANGO_MISSING_TEST}"})

    def test_unsupported_or_embedded_credential_urls_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute http\\(s\\)"):
            HttpMCPServer("remote", {"url": "file:///etc/passwd"})
        with self.assertRaisesRegex(ValueError, "require HTTPS"):
            HttpMCPServer("remote", {"url": "http://remote.example/mcp"})
        local = HttpMCPServer("local", {"url": "http://localhost:8765/mcp"})
        local.stop()
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            HttpMCPServer("remote", {"url": "https://user:password@example.test/mcp"})

    def test_guided_nango_setup_saves_config_without_echoing_api_key(self):
        saved = {}
        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        with (
            patch("sys.stdin", fake_stdin),
            patch("builtins.input", side_effect=["linear", "connection-1", ""]),
            patch("getpass.getpass", return_value="nango-private-token"),
            patch("niji.cli.load_mcp_servers", return_value={}),
            patch("niji.cli.save_mcp_servers", side_effect=lambda servers: saved.update(servers)),
            patch("builtins.print") as output,
        ):
            _cmd_connectors(["add", "nango"])
        self.assertEqual(saved["nango_linear"]["api_key"], "nango-private-token")
        self.assertEqual(saved["nango_linear"]["provider_config_key"], "linear")
        self.assertEqual(saved["nango_linear"]["connection_id"], "connection-1")
        self.assertNotIn("nango-private-token", " ".join(str(call) for call in output.call_args_list))

    def test_mcp_configuration_is_written_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".niji"
            file = root / "mcp.json"
            with patch("niji.config.CONFIG_DIR", root), patch("niji.config.MCP_FILE", file):
                save_mcp_servers({"test": {"transport": "stdio", "command": "echo"}})
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertEqual(json.loads(file.read_text())["servers"]["test"]["command"], "echo")

    def test_connect_all_keeps_existing_stdio_and_adds_http(self):
        transport = self._client([
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            {},
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}])
        with (
            patch("niji.mcp.httpx.Client", return_value=transport),
            patch("niji.mcp.MCPServer.start") as stdio_start,
            patch("builtins.print"),
        ):
            clients = connect_all({
                "remote": {"transport": "http", "url": "https://example.test/mcp"},
                "local": {"command": "unused", "args": []}})
        self.assertEqual(len(clients), 2)
        self.assertIsInstance(clients[0], HttpMCPServer)
        stdio_start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
