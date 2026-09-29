import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .config import (PRESETS, SESSION_DIR, load_config, load_mcp_servers,
                     resolve_provider, save_config)


def _first_run_setup(provider_hint=None):
    """Prompt for provider credentials on first interactive launch."""
    cfg = load_config()
    provider_names = list(PRESETS)
    selected = provider_hint or "openrouter"

    if provider_hint is None:
        print("\nWelcome to niji-agent! Let's set up your model provider first.")
        print("Choose a provider (OpenRouter is a convenient default):")
        for index, name in enumerate(provider_names, 1):
            print(f"  {index}. {name}")
        choice = input(f"Provider [default {provider_names.index('openrouter') + 1} - OpenRouter]: ").strip()
        if choice:
            if choice.isdigit() and 1 <= int(choice) <= len(provider_names):
                selected = provider_names[int(choice) - 1]
            elif choice.lower() in PRESETS:
                selected = choice.lower()
            else:
                raise SystemExit("Unknown provider selection. Run 'niji' again and choose a listed provider.")

    preset = PRESETS[selected]
    cfg["provider"] = selected
    if preset["env_key"]:
        try:
            key = getpass.getpass(f"Paste your {selected} API key (input hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("Setup cancelled. Run 'niji' when you're ready to configure it.")
        if not key:
            raise SystemExit("No API key entered. Run 'niji' again to finish setup.")
        cfg.setdefault("api_keys", {})[selected] = key

    save_config(cfg)
    print(f"[ok] Setup saved for {selected}. Starting niji...\n")


def _ensure_setup(args):
    cfg = load_config()
    provider = (args.provider or os.environ.get("NIJI_PROVIDER")
                or cfg.get("provider") or "openrouter")
    preset = PRESETS.get(provider)
    if not preset or not preset["env_key"] or args.api_key:
        return
    has_key = (os.environ.get(preset["env_key"])
               or os.environ.get("NIJI_API_KEY")
               or cfg.get("api_keys", {}).get(provider))
    if has_key:
        return
    if not sys.stdin.isatty():
        raise SystemExit("First-run setup needs an interactive terminal. Run 'niji' without arguments to configure your provider.")
    hint = provider if (args.provider or os.environ.get("NIJI_PROVIDER") or cfg.get("provider")) else None
    _first_run_setup(hint)


def _build_agent(args, mcp_path=None):
    from .agent import Agent
    from .mcp import connect_all
    provider = resolve_provider(args.provider, args.model, args.api_key)
    clients = connect_all(load_mcp_servers(mcp_path or getattr(args, "mcp", None)))
    agent = Agent(provider,
                  approval="ask" if args.ask else "auto",
                  max_turns=args.max_turns,
                  verbose=not args.quiet,
                  mcp_clients=clients)
    return agent, provider


def _list_sessions():
    if not SESSION_DIR.exists():
        print("no sessions yet")
        return
    files = sorted(SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for f in files[:20]:
        try:
            msgs = json.loads(f.read_text())
            first_user = next((m["content"] for m in msgs
                               if m.get("role") == "user"), "")[:60]
        except Exception:
            first_user = "?"
        print(f"  {f.stem}  ({f.stat().st_size // 1024} KB)  {first_user}")


def _interactive_chat(agent, provider):
    print(f"[niji] {provider['provider']}/{provider['model']} — chat mode")
    print("commands: /help /model /cost /compact /memory /exit (or 'exit')")
    while True:
        try:
            user = input("\nniji> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not user:
            continue
        if user in ("/exit", "exit", "quit"):
            break
        if user == "/help":
            print("Just type a task in plain language. Slash commands:")
            print("  /model    show current model")
            print("  /cost     token usage so far")
            print("  /compact  force-summarize old context")
            print("  /memory   show long-term memory")
            print("  /exit     quit")
            continue
        if user == "/model":
            print(f"{provider['provider']} / {agent.model}")
            continue
        if user == "/cost":
            print(agent.cost_line())
            continue
        if user == "/memory":
            from .config import MEMORY_FILE
            print(MEMORY_FILE.read_text(errors="replace")
                  if MEMORY_FILE.exists() else "[memory empty]")
            continue
        if user == "/compact":
            from .compaction import maybe_compact
            agent.messages, done = maybe_compact(
                agent.messages, agent.client, agent.model, force=True)
            print("[compacted]" if done else "[nothing to compact]")
            continue
        try:
            agent.chat(user)
            if not args_quiet():
                print(agent.cost_line())
        except KeyboardInterrupt:
            print("\n[interrupted]")


def args_quiet():
    return "--quiet" in sys.argv


def main():
    argv = sys.argv[1:]

    # ---------- subcommands ----------
    if argv and argv[0] == "providers":
        cfg = load_config()
        print("Providers (any OpenAI-compatible endpoint works):")
        for name, p in PRESETS.items():
            state = ("env:" + p["env_key"]) if p["env_key"] else "no key needed"
            if cfg.get("api_keys", {}).get(name):
                state = "stored in ~/.niji/config.json"
            print(f"  {name:12s} model={p['model']:42s} key={state}")
        print("Custom: export NIJI_BASE_URL=https://your-endpoint/v1 (works with any name)")
        return

    if argv and argv[0] == "sessions":
        _list_sessions()
        return

    if argv and argv[0] == "config":
        if len(argv) >= 4 and argv[1] == "set-key":
            cfg = load_config()
            cfg.setdefault("api_keys", {})[argv[2]] = argv[3]
            save_config(cfg)
            print(f"[ok] API key for '{argv[2]}' saved")
        elif len(argv) >= 3 and argv[1] == "set-default":
            cfg = load_config()
            cfg["provider"] = argv[2]
            save_config(cfg)
            print(f"[ok] default provider = {argv[2]}")
        else:
            print("usage: niji config set-key <provider> <api_key>")
            print("       niji config set-default <provider>")
        return

    # ---------- args ----------
    p = argparse.ArgumentParser(
        prog="niji",
        description="niji-agent — powerful provider-agnostic coding agent "
                    "(MCP connectors, subagents, memory, planning, parallel tools)")
    p.add_argument("task", nargs="*", help="Task in plain language (omit for chat)")
    p.add_argument("--provider", help="openai | openrouter | anthropic | gemini | groq | deepseek | together | ollama | custom (via NIJI_BASE_URL)")
    p.add_argument("--model", help="Override model name")
    p.add_argument("--api-key", help="Override API key")
    p.add_argument("--ask", action="store_true", help="Approve every bash command")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--resume", help="Resume a saved session id (see: niji sessions)")
    p.add_argument("--continue", dest="cont", action="store_true",
                   help="Resume the most recent session")
    p.add_argument("--mcp", help="Path to a custom mcp.json")
    p.add_argument("--no-mcp", action="store_true", help="Skip MCP connectors")
    args = p.parse_args(argv)
    _ensure_setup(args)

    mcp_path = None if args.no_mcp else args.mcp

    if args.resume or args.cont:
        sid = args.resume
        if args.cont:
            files = sorted(SESSION_DIR.glob("*.json"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            if not files:
                print("no sessions to continue")
                return
            sid = files[0].stem
        f = SESSION_DIR / f"{sid}.json"
        if not f.exists():
            print(f"session not found: {sid}  (see: niji sessions)")
            return
        agent, provider = _build_agent(args, mcp_path)
        agent.resume(json.loads(f.read_text()))
        agent.session_id = sid
        print(f"[niji] resumed session {sid} ({len(agent.messages)} messages)")
        _interactive_chat(agent, provider)
        return

    task = " ".join(args.task).strip()
    agent, provider = _build_agent(args, mcp_path)

    if task:
        print(f"[niji] {provider['provider']}/{provider['model']}")
        try:
            agent.chat(task)
        finally:
            print(agent.cost_line())
            for c in agent.mcp_clients:
                c.stop()
        return

    try:
        _interactive_chat(agent, provider)
    finally:
        for c in agent.mcp_clients:
            c.stop()


if __name__ == "__main__":
    main()
