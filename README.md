# Niji Agent 🌈 — v2.4.1

A provider-agnostic terminal coding agent with interactive setup, plain-language tasks, slash commands, MCP connectors, planning, memory, sessions, and subagents.

## Install on Termux

Install prerequisites and the latest public release directly from GitHub:

```sh
pkg install curl git python -y
curl -fsSL https://raw.githubusercontent.com/stanuser297/niji-agent/main/install.sh | sh
niji
```

Requires Python 3.10+. First launch opens the setup wizard. Pick a provider, enter its API key (visible input is the Termux-friendly default; hidden entry is optional), and choose a model. The key is saved locally in `~/.niji/config.json` with private file permissions—no `export` command is needed. Ollama can be used without an API key.

For other systems, the installer is also available as `install.sh`. It installs the GitHub `main` branch and prints the installed version.

## What's new in 2.4.1

- Groq defaults to active `openai/gpt-oss-120b`; the retired `llama-3.3-70b-versatile` saved model is migrated automatically
- Provider-specific Groq 404/401 guidance separates retired-model errors from invalid key/account authorization

## What's new in 2.4.0

- Screenshot-inspired command-center dashboard: agent profile, actual tool catalog and per-session tool use, recent activity, live session status, and quick commands
- Niji's own cyan/blue identity and original wordmark; wide side-by-side panels and mobile stacked layout
- Tool calls, uptime, and task activity are real session metrics; unavailable Skills/CPU/RAM statistics are not fabricated
- API-key input defaults to visible typing for reliable Termux use, with optional hidden mode

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
