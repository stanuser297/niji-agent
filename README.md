# Niji Agent 🌈 — v2.7.6

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

## What's new in 2.7.6

- The assistant is instructed to handle ordinary, harmless requests without generic refusals, interpret Hinglish/typos from context, and use public web sources for live/trending questions when available. It must disclose lookup failures and never claim an unperformed search.

## What's new in 2.7.5

- Fixed a chat-exit traceback after backspace: an editing cursor variable was shadowing the saved terminal settings. Added PTY regression checks that terminal echo/canonical settings are restored after editing.

## What's new in 2.7.4

- Fixed the input caret being shifted three columns to the right inside the “Ask anything” composer. It now starts before the placeholder and tracks the typed-text cursor exactly.

## What's new in 2.7.3

- User turns now print in the chat scrollback above the pinned composer instead of disappearing when the input resets.
- Left/right cursor editing, backspace, and forward-delete operate on whole visible characters/grapheme clusters, including emoji and combining-script text; cursor placement is measured in terminal cells.

## What's new in 2.7.2

- Fixed duplicate/stacking chat frames by redrawing the editor with absolute cursor positioning and clearing its exact rows.
- Reserved a fixed bottom panel for the composer and live session details; model responses scroll in the region above it. Normal terminal scrolling is restored when the interactive chat exits.

## What's new in 2.7.1

- Fixed the fresh-install crash in the branded chat composer by declaring `wcwidth` as an explicit runtime dependency; the composer imports it directly for correct terminal-cell width handling.
- Installer smoke-check now imports the chat composer before launching, so a missing UI dependency is caught during installation instead of after it.

## What's new in 2.7.0

- Branded inline chat composer inspired by the supplied reference: cyan/violet Niji frame, focused message input, and a live session-details strip below it.
- Footer shows the active model/provider, context or token usage, agent, Python runtime, tool count, and latest request/session time; fields wrap cleanly on narrow Termux screens.
- Editable terminal input supports cursor movement, history, delete/backspace, common Ctrl shortcuts, and bracketed clipboard paste.

## What's new in 2.6.0

- Provider setup and `/model` use an unbuffered terminal-byte picker that handles CSI and SS3 arrow sequences used by Android/Termux terminals; `j/k`, Page Up/Down, Enter and Esc/q are supported. Non-interactive terminals fall back to typing the provider/model name.
- Provider failures now give recovery steps: correct bad model/routes (400/404), replace credentials (401), check permissions (403), compact oversized context (413), and distinguish rate limiting from exhausted quota (429).
- SDK hidden retries are disabled. Niji performs at most one retry for transient connection/timeout/rate/server errors, adds jitter, respects short `Retry-After` hints, and defers rather than retrying early after long provider delays. Invalid-key/model/permission errors and quota/billing failures are not blindly retried.
- Bounded execution defaults: 20 model turns, 30 tool calls per user request, at most 6 tools per model turn; subagents get tighter caps. Shell commands are capped at 120 seconds, file reads at 1,000 lines, and fetched page output at 15,000 characters. `/limits` shows the active budgets; `--max-turns`, `--max-tool-calls`, and `--max-tool-calls-per-turn` can lower or raise them within hard caps.
- Failed initial provider prompts are removed from saved chat history so a corrected retry does not append a malformed consecutive user message; completed tool actions are retained and reported if a later model request fails.
- Live activity now shows retry/limit events in the dashboard and `/activity` feed.

Research references: [OpenAI API error codes](https://developers.openai.com/api/docs/guides/error-codes), [rate limits and retry guidance](https://developers.openai.com/api/docs/guides/rate-limits), [OpenAI Python SDK retry settings](https://github.com/openai/openai-python#retries), and [Python terminal cbreak mode](https://docs.python.org/3/library/tty.html).

## What's new in 2.5.1

- Model activation probes the selected chat model and applies it to the active session only when accepted (or after explicit confirmation for a non-auth probe failure)
- Live phase feed reports thinking, tool start/completion, errors and response completion; `/activity` shows the recent event history

## What's new in 2.5.0

- `/model` interactively browses preset and custom providers, fetches the selected provider's available model IDs, tests the choice, then switches and saves it without leaving the chat
- `/models` and `niji models [provider]` show full accessible catalogs when the provider exposes a compatible models endpoint; unconfigured providers are marked, and manual model entry remains available
- `/approval [ask|auto]` toggles tool confirmation during a session; `ask` is confirmation, not a security sandbox
- Loads the current workspace's `AGENTS.md` as project-specific guidance and reminds the agent to inspect diffs and run relevant checks after edits

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
niji --max-turns 12 --max-tool-calls 20       # set lower per-request budgets
niji --continue                           # resume latest session
niji sessions                             # list saved sessions
niji setup                                # run provider setup again
niji doctor                               # diagnose setup
niji providers                            # list available providers
niji models                               # list catalogs for connected providers
niji models groq                          # list Groq model IDs
niji providers add                        # add a custom provider
niji providers use openrouter             # switch default provider
```

Interactive slash commands: `/help`, `/model` (browse/switch provider and model with arrows), `/models`, `/approval [ask|auto]`, `/activity`, `/limits`, `/status`, `/tools`, `/setup`, `/doctor`, `/cost`, `/compact`, `/memory`, `/sessions`, `/clear`, `/exit`.

The request budgets reset for each new user prompt. Defaults are capped at 20 model turns, 30 executed tools, and 6 tools from any one model response; hard limits prevent configuration above 100 turns / 100 tools / 20 tools per response. These are cost/loop guardrails, not an OS sandbox: commands still run with your account's permissions. Use `--ask` for confirmations, inspect commands before approving, and keep backups for important files.

Model discovery uses each connected provider's compatible models endpoint when available. Some providers hide catalogs or require manual model IDs; the picker explains that and keeps manual entry available. No API keys are shown in catalog output.

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
