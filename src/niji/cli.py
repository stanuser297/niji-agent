import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from . import __version__
from .ui import render_home
from .config import (CONFIG_DIR, CONFIG_FILE, MCP_FILE, PRESETS, SESSION_DIR,
                     load_config, load_mcp_servers, resolve_provider,
                     save_config)
from .model_catalog import (fetch_provider_models, provider_is_configured,
                            provider_names, resolve_catalog_provider)


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


def _cmd_models(argv):
    """List provider model catalogs; unconfigured providers are clearly marked."""
    cfg = load_config()
    requested = argv[2] if len(argv) >= 3 else None
    names = provider_names(cfg)
    if requested:
        if requested not in names:
            print(f"Unknown provider '{requested}'. See: niji providers")
            return True
        names = [requested]

    console = Console()
    for name in names:
        preset = PRESETS.get(name, {})
        custom = cfg.get("custom_providers", {}).get(name, {})
        active = cfg.get("models", {}).get(name) or custom.get("model") or preset.get("model", "(manual)")
        console.print(f"\n[bold cyan]{name}[/]" + (" [green](default)[/]" if cfg.get("provider") == name else ""))
        if not provider_is_configured(name, cfg):
            console.print(f"  [dim]Not connected. Preset model: {active}. Run `niji setup` to connect.[/]")
            continue
        resolved, error = resolve_catalog_provider(name)
        if error:
            console.print(f"  [yellow]{error}[/]")
            continue
        active = resolved.get("model", active)
        models, message = fetch_provider_models(resolved)
        if not models:
            console.print(f"  [yellow]{message}[/]")
            console.print(f"  [dim]Configured model: {active}[/]")
            continue
        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Model ID", style="white")
        table.add_column("State", style="green")
        for idx, model_id in enumerate(models, 1):
            table.add_row(str(idx), escape(model_id), "active" if model_id == active else "")
        console.print(table)
    if not requested:
        console.print("\n[dim]Catalogs require a connected provider key and a compatible `/models` endpoint. "
                      "Use `niji models <provider>` to inspect one; `/model` to switch.[/]")
    return True


def _activate_model(agent, provider, name, model_id):
    """Validate a selected model, then persist and switch the live session."""
    try:
        provider_cfg = resolve_provider(name, model=model_id)
    except SystemExit as exc:
        Console().print(f"[yellow]{exc}[/]")
        Console().print("Run `/setup` to connect this provider first.")
        return False
    from .setup_wizard import _connection_guidance, test_connection
    with Console().status(f"[cyan]Testing {name}/{model_id}...[/]"):
        ok, message = test_connection(provider_cfg)
    if not ok:
        Console().print(Panel(_connection_guidance(provider_cfg, message),
                              title="Model switch not applied", border_style="yellow"))
        return False

    cfg = load_config()
    cfg["provider"] = name
    if name in PRESETS:
        cfg.setdefault("models", {})[name] = model_id
    elif name in cfg.get("custom_providers", {}):
        cfg["custom_providers"][name]["model"] = model_id
    save_config(cfg)

    from openai import OpenAI
    agent.client = OpenAI(api_key=provider_cfg["api_key"],
                          base_url=provider_cfg["base_url"])
    agent.model = model_id
    agent.provider_name = name
    agent.provider_cfg = provider_cfg
    provider.clear()
    provider.update(provider_cfg)
    agent.messages.append({"role": "system", "content":
                           f"The active model was changed to {name}/{model_id}. Continue the same task and conversation."})
    Console().print(f"[green]✓ Switched to {name}/{model_id}[/] — {message}")
    _render_home(agent, provider)
    return True


def _interactive_model_picker(agent, provider, provider_name=None, requested_model=None):
    """Browse providers and their live model lists, with manual-ID fallback."""
    cfg = load_config()
    names = provider_names(cfg)
    console = Console()
    name = provider_name
    if name is None:
        table = Table(title="Providers", show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Provider")
        table.add_column("Status")
        for idx, candidate in enumerate(names, 1):
            preset = PRESETS.get(candidate, {})
            custom = cfg.get("custom_providers", {}).get(candidate, {})
            status = "connected" if provider_is_configured(candidate, cfg) else "setup needed"
            default_model = cfg.get("models", {}).get(candidate) or custom.get("model") or preset.get("model", "")
            table.add_row(str(idx), candidate + (" ★" if candidate == provider.get("provider") else ""),
                          f"{status} · {default_model}")
        console.print(table)
        current = provider.get("provider")
        default_idx = str(names.index(current) + 1) if current in names else "1"
        choice = Prompt.ask("Choose provider number or name", default=default_idx).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            name = names[int(choice) - 1]
        elif choice in names:
            name = choice
        else:
            console.print("[yellow]Unknown provider choice.[/]")
            return
    elif name not in names:
        console.print(f"[yellow]Unknown provider '{name}'. Use `/providers` to see options.[/]")
        return

    provider_cfg, error = resolve_catalog_provider(name)
    if error:
        console.print(f"[yellow]{name}: {error}[/]")
        return

    model_ids, catalog_message = fetch_provider_models(provider_cfg)
    if requested_model is None and model_ids:
        table = Table(title=f"{name} models ({len(model_ids)} available)",
                      show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Model ID")
        table.add_column("State", style="green")
        for idx, model_id in enumerate(model_ids, 1):
            table.add_row(str(idx), escape(model_id),
                          "current" if model_id == provider_cfg.get("model") else "")
        console.print(table)
        choice = Prompt.ask("Choose number, or `m` to enter a model ID manually").strip()
        if choice.lower() == "m":
            requested_model = Prompt.ask("Model ID").strip()
        elif choice.isdigit() and 1 <= int(choice) <= len(model_ids):
            requested_model = model_ids[int(choice) - 1]
        elif choice in model_ids:
            requested_model = choice
        else:
            console.print("[yellow]Invalid model choice; no changes made.[/]")
            return
    elif requested_model is None:
        console.print(f"[yellow]{catalog_message}[/]")
        requested_model = Prompt.ask("Enter model ID manually", default=provider_cfg["model"]).strip()

    if not requested_model:
        console.print("[yellow]No model ID entered; no changes made.[/]")
        return
    _activate_model(agent, provider, name, requested_model)


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


# ---------------- interactive terminal UI ----------------


def _render_home(agent, provider, quiet=False):
    render_home(agent, provider, quiet=quiet)


def _show_help():
    console = Console()
    table = Table(title="Niji commands", show_header=True, header_style="bold cyan")
    table.add_column("Command", style="green", no_wrap=True)
    table.add_column("What it does")
    rows = [
        ("/help", "Show this command list"),
        ("/status", "Show provider, model, workspace and usage"),
        ("/tools", "List built-in and connected MCP tools"),
        ("/model", "Browse providers and their available models; switch for this session"),
        ("/models", "List model catalogs for configured providers"),
        ("/approval", "Toggle auto/ask confirmation mode for tool execution"),
        ("/cost", "Show token usage so far"),
        ("/compact", "Summarize older context to free space"),
        ("/memory", "View saved long-term memory"),
        ("/setup", "Reconnect/switch provider and model"),
        ("/providers", "List provider choices"),
        ("/doctor", "Test the saved provider and setup"),
        ("/sessions", "List saved sessions"),
        ("/clear", "Clear the screen and redraw the home panel"),
        ("/exit", "Save and leave Niji"),
    ]
    for command, description in rows:
        table.add_row(command, description)
    console.print(table)
    console.print("[dim]Or just type what you want Niji to do.[/]")


def _show_provider_error(provider, exc):
    console = Console(stderr=True)
    status = getattr(exc, "status_code", None)
    provider_name = str(provider.get("provider", ""))
    base_url = str(provider.get("base_url", ""))
    groq = provider_name == "groq" or "api.groq.com" in base_url
    if status == 404 and (provider_name == "nvidia" or "nvidia.com" in base_url):
        detail = ("NVIDIA returned 404. Verify the model ID and base URL. "
                  "For GLM 5.3 Flash use `z-ai/glm-5.3-flash` (with dots), "
                  "then run `/setup` to save it.")
    elif status == 404 and groq:
        model = str(provider.get("model", "(unknown)"))
        if model == "llama-3.3-70b-versatile":
            detail = ("Groq returned 404. `llama-3.3-70b-versatile` was shut down for "
                      "developer/free-tier accounts on August 16, 2026. Use model "
                      "`openai/gpt-oss-120b` with base URL `https://api.groq.com/openai/v1`, "
                      "then run `/setup`.")
        else:
            detail = (f"Groq returned 404 for model `{model}`. Check that the exact model ID "
                      "is available to your account and use base URL "
                      "`https://api.groq.com/openai/v1`; run `/setup` to change it.")
    elif status == 404:
        detail = ("The provider returned 404. Check the OpenAI-compatible base URL "
                  "and exact model ID, then run `/setup`.")
    elif status in (401, 403) and groq:
        detail = ("Groq rejected the API key or account access (HTTP %s). Replace it with a "
                  "fresh GroqCloud API key in `/setup`, and confirm the account has API access. "
                  "The saved key is never shown here." % status)
    elif status in (401, 403):
        detail = "The provider rejected the API key or account permissions. Run `/setup` to replace the key."
    elif status == 429:
        detail = "The provider rate limit or quota was reached. Check the account quota and try again."
    else:
        detail = str(exc)
    console.print(Panel(Text(detail), title="Request failed — chat is still open",
                        border_style="red"))


def _interactive_chat(agent, provider, quiet=False):
    console = Console()
    _render_home(agent, provider, quiet)
    while True:
        try:
            user = Prompt.ask("\n[bold cyan]you ❯[/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Niji saved. See you next time.[/]")
            break
        if not user:
            continue
        if user.lower() in ("/exit", "exit", "quit", "/quit"):
            break
        if user == "/help":
            _show_help()
            continue
        if user in ("/model", "/provider"):
            _interactive_model_picker(agent, provider)
            continue
        if user == "/models":
            _cmd_models(["niji", "models"])
            continue
        if user.startswith("/model "):
            parts = user.split(maxsplit=2)
            if len(parts) >= 2 and parts[1].lower() == "list":
                _cmd_models(["niji", "models", parts[2] if len(parts) > 2 else provider["provider"]])
            elif len(parts) >= 2:
                _interactive_model_picker(agent, provider, parts[1],
                                          parts[2] if len(parts) > 2 else None)
            continue
        if user == "/approval" or user.startswith("/approval "):
            parts = user.split(maxsplit=1)
            if len(parts) == 1:
                agent.approval = "ask" if agent.approval != "ask" else "auto"
            elif parts[1].strip().lower() in ("ask", "auto"):
                agent.approval = parts[1].strip().lower()
            else:
                console.print("Usage: /approval [ask|auto]")
                continue
            if agent.approval == "ask":
                console.print("[yellow]Approval mode: ask — tool actions need confirmation. This is not a sandbox.[/]")
            else:
                console.print("[green]Approval mode: auto — tools can execute actions without per-action confirmation.[/]")
            continue
        if user == "/status":
            _render_home(agent, provider, quiet=False)
            console.print(agent.cost_line())
            continue
        if user == "/tools":
            names = [schema.get("function", {}).get("name", "tool")
                     for schema in agent.tool_schemas]
            console.print(Panel(Text("\n".join(names) or "No tools available"),
                                title=f"Available tools ({len(names)})", border_style="blue"))
            continue
        if user == "/cost":
            console.print(agent.cost_line())
            continue
        if user == "/memory":
            from .config import MEMORY_FILE
            console.print(MEMORY_FILE.read_text(errors="replace")
                          if MEMORY_FILE.exists() else "[dim]Memory is empty.[/]")
            continue
        if user == "/compact":
            from .compaction import maybe_compact
            agent.messages, done = maybe_compact(
                agent.messages, agent.client, agent.model, force=True)
            console.print("[green]Context compacted.[/]" if done else "[dim]Nothing to compact.[/]")
            continue
        if user == "/clear":
            console.clear()
            _render_home(agent, provider, quiet)
            continue
        if user == "/providers":
            _cmd_providers(["niji", "providers"])
            continue
        if user == "/doctor":
            _cmd_doctor()
            continue
        if user == "/sessions":
            _list_sessions()
            continue
        if user == "/setup":
            try:
                from .setup_wizard import run_setup
                from openai import OpenAI
                new_provider = run_setup()
                agent.provider_cfg = new_provider
                agent.client = OpenAI(api_key=new_provider["api_key"],
                                      base_url=new_provider["base_url"])
                agent.model = new_provider["model"]
                agent.provider_name = new_provider["provider"]
                provider.update(new_provider)
                console.print("[green]Provider switched. Continue chatting.[/]")
                _render_home(agent, provider, quiet)
            except SystemExit as exc:
                console.print(f"[yellow]{exc}[/]")
            continue
        try:
            console.print("\n[bold green]niji ❯[/]")
            agent.chat(user)
            if not quiet:
                console.print(f"[dim]{agent.cost_line()}[/]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Request interrupted.[/]")
        except Exception as exc:
            _show_provider_error(provider, exc)


# ---------------- main ----------------

def main():
    argv = sys.argv[1:]

    # ---------- subcommands ----------
    if argv and argv[0] == "providers":
        _cmd_providers(argv)
        return
    if argv and argv[0] == "models":
        _cmd_models(["niji", *argv])
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
        Console().print(f"[green]Resumed session[/] {sid} ({len(agent.messages)} messages)")
        try:
            _interactive_chat(agent, provider, args.quiet)
        finally:
            for client in agent.mcp_clients:
                client.stop()
        return

    task = " ".join(args.task).strip()
    agent, provider = _build_agent(args, mcp_path)

    if task:
        Console().print(f"[bold green]niji[/] • {provider['provider']}/{provider['model']}")
        try:
            agent.chat(task)
        except Exception as exc:
            _show_provider_error(provider, exc)
            sys.exit(1)
        finally:
            Console().print(f"[dim]{agent.cost_line()}[/]")
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
