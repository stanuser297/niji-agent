import argparse
import json
import sys

from . import __version__
from .config import (CONFIG_DIR, CONFIG_FILE, MCP_FILE, PRESETS, SESSION_DIR,
                     load_config, load_mcp_servers, resolve_provider,
                     save_config)


def _build_agent(args, mcp_path=None):
    from .agent import Agent
    from .mcp import connect_all
    provider = resolve_provider(getattr(args, "provider", None),
                                getattr(args, "model", None),
                                getattr(args, "api_key", None))
    clients = [] if getattr(args, "no_mcp", False) or mcp_path == "skip" \
        else connect_all(load_mcp_servers(mcp_path or getattr(args, "mcp", None)))
    agent = Agent(provider,
                  approval="ask" if getattr(args, "ask", False) else "auto",
                  max_turns=getattr(args, "max_turns", 60),
                  verbose=not getattr(args, "quiet", False),
                  mcp_clients=clients)
    return agent, provider


# ---------------- provider management ----------------

def _cmd_providers(argv):
    cfg = load_config()
    custom = cfg.get("custom_providers", {})
    if len(argv) >= 2 and argv[1] == "add":
        _provider_add()
        return True
    if len(argv) >= 3 and argv[1] == "remove":
        name = argv[2]
        changed = False
        for section in ("custom_providers", "api_keys", "models"):
            if name in cfg.get(section, {}):
                del cfg[section][name]
                changed = True
        if cfg.get("provider") == name:
            cfg.pop("provider", None)
            changed = True
        save_config(cfg)
        print(f"[{'ok' if changed else 'no change'}] provider '{name}' removed")
        return True
    if len(argv) >= 3 and argv[1] == "use":
        name = argv[2]
        if name not in PRESETS and name not in custom:
            print(f"unknown provider '{name}' — see: niji providers")
            return True
        cfg["provider"] = name
        save_config(cfg)
        print(f"[ok] default provider = {name}")
        return True
    if len(argv) == 1 or argv[1] == "list":
        default = cfg.get("provider", "(none)")
        print(f"{'NAME':14s} {'MODEL':40s} KEY")
        for name, p in PRESETS.items():
            state = ("env:" + p["env_key"]) if p["env_key"] else "no key needed"
            if cfg.get("api_keys", {}).get(name):
                state = "stored"
            tag = "*" if name == default else " "
            print(f"{tag}{name:13s} {p['model']:40s} {state}")
        for name, c in custom.items():
            tag = "*" if name == default else " "
            print(f"{tag}{name:13s} {c.get('model', 'default'):40s} "
                  f"custom {c['base_url']}")
        print("\n* = default. Manage: niji providers add | use <name> | remove <name>")
        return True
    print("usage: niji providers [list] | add | use <name> | remove <name>")
    return True


def _provider_add():
    from .setup_wizard import _wizard_custom_provider
    cfg = load_config()
    custom = cfg.setdefault("custom_providers", {})
    name, settings = _wizard_custom_provider()
    if name in PRESETS or name in custom:
        print(f"'{name}' already exists")
        return
    custom[name] = settings
    cfg["provider"] = name
    save_config(cfg)
    print(f"[ok] provider '{name}' added and set as default")


# ---------------- doctor ----------------

def _cmd_doctor():
    from .setup_wizard import test_connection
    print("niji doctor — checking your setup\n")
    ok_all = True

    def check(label, ok, fix=""):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(f"  {'✓' if ok else '✗'} {label}" + (f"  → {fix}" if not ok and fix else ""))

    check("config file exists", CONFIG_FILE.exists(),
          "run: niji setup")
    cfg = load_config()
    name = cfg.get("provider")
    check("default provider set", bool(name),
          "run: niji providers use <name>")
    if name:
        try:
            p = resolve_provider()
            preset = PRESETS.get(name)
            if preset and preset.get("env_key"):
                check(f"API key for '{name}'", bool(p["api_key"] and p["api_key"] not in ("custom",)),
                      f"run: niji config set-key {name} sk-...")
            ok, msg = test_connection(p)
            check(f"connection ({p['model']})", ok, msg)
        except SystemExit as e:
            check(f"resolve provider '{name}'", False, str(e))
    mcp_valid = True
    mcp = {}
    if MCP_FILE.exists():
        try:
            raw_mcp = json.loads(MCP_FILE.read_text())
            mcp = raw_mcp.get("servers", raw_mcp) if isinstance(raw_mcp, dict) else None
            mcp_valid = isinstance(mcp, dict)
        except Exception:
            mcp_valid = False
    check("mcp.json valid", mcp_valid,
          "check ~/.niji/mcp.json syntax and ensure it contains an object")
    if mcp_valid and mcp:
        print(f"  • {len(mcp)} MCP connector(s) configured")
    print("\n" + ("[ok] all good — happy coding!" if ok_all else "[!] fix the items above"))
    return ok_all


# ---------------- sessions ----------------

def _list_sessions():
    if not SESSION_DIR.exists():
        print("no sessions yet")
        return
    files = sorted(SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for f in files[:20]:
        try:
            msgs = json.loads(f.read_text())
            first_user = next((str(m.get("content")) for m in msgs
                               if m.get("role") == "user"), "?")[:60]
        except Exception:
            first_user = "?"
        print(f"  {f.stem}  ({f.stat().st_size // 1024} KB)  {first_user}")


# ---------------- chat ----------------

def _interactive_chat(agent, provider, quiet=False):
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
            if not quiet:
                print(agent.cost_line())
        except KeyboardInterrupt:
            print("\n[interrupted]")


# ---------------- main ----------------

def main():
    argv = sys.argv[1:]

    # ---------- subcommands ----------
    if argv and argv[0] == "providers":
        _cmd_providers(argv)
        return
    if argv and argv[0] == "doctor":
        ok = _cmd_doctor()
        sys.exit(0 if ok else 1)
    if argv and argv[0] == "setup":
        from .setup_wizard import run_setup
        run_setup()
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
    p.add_argument("--version", action="version", version=f"niji-agent {__version__}")
    p.add_argument("task", nargs="*", help="Task in plain language (omit for chat)")
    p.add_argument("--provider", help="openai | openrouter | anthropic | ... | any custom name")
    p.add_argument("--model", help="Override model name")
    p.add_argument("--api-key", help="Override API key")
    p.add_argument("--ask", action="store_true", help="Ask before shell, file-write, network, and MCP actions")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--resume", help="Resume a saved session id (see: niji sessions)")
    p.add_argument("--continue", dest="cont", action="store_true",
                   help="Resume the most recent session")
    p.add_argument("--mcp", help="Path to a custom mcp.json")
    p.add_argument("--no-mcp", action="store_true", help="Skip MCP connectors")
    args = p.parse_args(argv)

    # ---------- first-run setup (Claude Code style) — runs BEFORE provider resolution ----------
    from .setup_wizard import needs_setup, run_setup
    if needs_setup(provider_name=args.provider, api_key=args.api_key):
        if not sys.stdin.isatty():
            print("No provider configured. Run: niji setup")
            sys.exit(1)
        run_setup(provider_name=args.provider)
        print()

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
        _interactive_chat(agent, provider, args.quiet)
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
        _interactive_chat(agent, provider, args.quiet)
    finally:
        for c in agent.mcp_clients:
            c.stop()


if __name__ == "__main__":
    main()
