# Niji Agent 🌈 — v2.2.3

A provider-agnostic terminal coding agent with interactive setup, plain-language tasks, slash commands, MCP connectors, planning, memory, sessions, and subagents.

## Install on Termux

Install prerequisites and the latest public release directly from GitHub:

```sh
pkg install curl git python -y
curl -fsSL https://raw.githubusercontent.com/stanuser297/niji-agent/main/install.sh | sh
niji
```

Requires Python 3.10+. First launch opens the setup wizard. Pick a provider, enter its API key (hidden while typing), and choose a model. The key is saved locally in `~/.niji/config.json` with private file permissions—no `export` command is needed. Ollama can be used without an API key.

For other systems, the installer is also available as `install.sh`. It installs the GitHub `main` branch and prints the installed version.

## What's new in 2.2.3

- API-key entry defaults to normal visible typing for reliable Android/Termux input; hidden entry remains available
- Clearly warns before a key is displayed, and allows a retry if the visible field is left blank
- Branded setup screen and provider-aware NVIDIA 401/403 diagnostics
- A failed connection test does not overwrite saved provider settings

## What's new in 2.0

- First-run provider setup with connection test and model selection
- Original Niji terminal identity: a responsive cyan/violet/amber dashboard, custom ribbon mark, session overview, active-tools panel, and mobile-friendly stacked layout
- Live chat controls and `/help`, `/status`, `/tools`, `/setup`, `/doctor`, and `/clear` commands
- NVIDIA NIM preset with the verified GLM model ID `z-ai/glm-5.3-flash`
- Add, list, switch, and remove custom OpenAI-compatible providers
- `niji doctor` checks the saved provider and connector configuration
- Keeps task planning, subagents, MCP tools, persistent memory, resumable sessions, streaming, token counts, and context compaction
- More restrictive local permissions for saved config/session/memory data; child processes do not inherit common API-key/token environment variables by default
- `--ask` now requests confirmation for shell, file-write, network-fetch, and MCP actions; parallel execution is limited to read-only calls

## Use

```sh
niji                                      # interactive chat
niji "Find and fix the bug in this project" # one-shot task
niji --ask "Review the project and suggest fixes" # confirm side effects
niji --continue                           # resume latest session
niji sessions                             # list saved sessions
niji setup                                # run provider setup again
niji doctor                               # diagnose setup
niji providers                            # list available providers
niji providers add                        # add a custom provider
niji providers use openrouter             # switch default provider
```

Interactive slash commands: `/help`, `/model`, `/cost`, `/compact`, `/memory`, `/exit`.

Use the tool only in directories where you trust it to read and modify files. It runs with your operating-system account's permissions; it is not a sandbox. MCP servers are separate programs, so only configure servers you trust. `--ask` adds confirmations, but does not turn the operating system into a sandbox.

## MCP connectors

Create `~/.niji/mcp.json`. Put connector-specific credentials in that server's `env` object; common credential variables from the parent environment are not inherited by default.

```json
{
  "servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "your-token"}
    }
  }
}
```

## License

MIT. See `LICENSE`.
