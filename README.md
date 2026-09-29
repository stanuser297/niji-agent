# niji-agent 🌙

**Provider-agnostic autonomous coding agent** — kisi bhi LLM provider par chalta hai,
aur features mein kisi bhi "Hermes-style" harness se aage.

## Feature comparison

| Feature | Typical harness | **niji-agent** |
|---|---|---|
| Provider-agnostic (OpenAI-compatible) | ✅ | ✅ (8 presets + custom via `NIJI_BASE_URL`) |
| Tools (bash/files/search) | ✅ | ✅ (9 built-in) |
| **MCP connectors** (GitHub, Postgres, filesystem, Slack...) | ❌ | ✅ — koi bhi MCP server |
| **Subagents** (fresh context for subtasks, depth-limited) | ❌/partial | ✅ |
| **Task planning** (todos) | ❌ | ✅ |
| **Long-term memory** (across sessions) | ❌ | ✅ `~/.niji/MEMORY.md` |
| **Session save/resume** | ❌ | ✅ `niji sessions`, `niji --continue` |
| **Parallel tool execution** | ❌ | ✅ |
| Auto-retry (rate limits) | ❌ | ✅ exponential backoff |
| Streaming + token/cost tracking | partial | ✅ `/cost` |
| Context compaction | ✅ | ✅ (force with `/compact`) |
| Safety denylist + `--ask` approval | ✅ | ✅ |
| Web fetch | ❌ | ✅ `web_fetch` |
| Image reading (vision) | ❌ | ✅ `read_image` |

## Install

```bash
cd niji-agent
pip install .
```

## Setup

First launch pe `niji` setup wizard kholta hai: provider choose karo, API key paste karo, phir chat start ho jaati hai. Key `~/.niji/config.json` mein locally save hoti hai; environment variable export karna zaroori nahi. Config file private permissions ke saath save hoti hai.

```bash
niji
# Local Ollama ke liye pehle: ollama pull llama3.1
```

Non-interactive setup ya existing config ke liye manual commands bhi available hain:

```bash
niji config set-key openrouter sk-or-...
niji config set-default openrouter
```

## Use

```bash
niji "project mein bug fix karke tests run kar"
niji --provider anthropic --model claude-sonnet-4-5 "add logging to src/"
niji --ask "setup the dev environment"        # har command pe approval
niji                                          # interactive chat
niji --continue                               # pichli session resume
niji sessions                                 # saved sessions list
```

Chat mein slash commands: `/model` `/cost` `/compact` `/memory` `/help`

## MCP connectors (jaise Claude Code ke)

`~/.niji/mcp.json` banao:

```json
{
  "servers": {
    "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
               "env": {"GITHUB_TOKEN": "ghp_..."}},
    "pg": {"command": "uvx", "args": ["mcp-server-postgres", "postgresql://localhost/db"]}
  }
}
```

Har MCP tool automatically model ko mil jata hai as `github__create_issue` style calls.
Ek server crash ho to baaki sab chalte rehte hain. `--no-mcp` se skip.

## Architecture

```
niji/
├── cli.py         # terminal UI: one-shot, chat, sessions, resume, slash commands
├── agent.py       # agentic loop: streaming, parallel tools, retries, subagents, usage
├── mcp.py         # MCP stdio client (connectors)
├── config.py      # provider presets, ~/.niji/config.json, mcp.json loader
├── safety.py      # always-on command denylist
├── compaction.py  # context management
└── tools/
    ├── builtin.py   # bash, read/write/edit file, list, grep, glob, web_fetch, read_image
    └── stateful.py  # todos (plan), task (subagents), memory_read/write
```

## License

MIT
