"""First-run setup wizard and provider connection diagnostics."""
import getpass
import os
import re
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from . import __version__

console = Console()

WIZARD_PROVIDERS = [
    ("openrouter", "OpenRouter (recommended — one key, many models)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic (Claude)"),
    ("gemini", "Google Gemini"),
    ("groq", "Groq"),
    ("deepseek", "DeepSeek"),
    ("together", "Together AI"),
    ("ollama", "Ollama (local, free, no API key)"),
]

BANNER = (
    f"[bold cyan]niji-agent[/] [dim]v{__version__}[/] — provider-agnostic coding agent\n"
    "[dim]MCP connectors • subagents • memory • planning • diagnostics[/]"
)


def needs_setup(provider_name: str | None = None, api_key: str | None = None) -> bool:
    """Check whether a first-run wizard is needed for the selected provider."""
    if api_key:
        return False
    from .config import PRESETS, load_config

    cfg = load_config()
    name = provider_name or os.environ.get("NIJI_PROVIDER") or cfg.get("provider")
    if name:
        preset = PRESETS.get(name)
        if preset:
            env_key = preset.get("env_key")
            if not env_key:
                return False
            return not bool(cfg.get("api_keys", {}).get(name)
                            or os.environ.get(env_key)
                            or os.environ.get("NIJI_API_KEY"))
        if cfg.get("custom_providers", {}).get(name) or os.environ.get("NIJI_BASE_URL"):
            return False
        # Unknown provider names should reach resolve_provider for a useful error.
        return False

    if os.environ.get("NIJI_API_KEY") or os.environ.get("NIJI_BASE_URL"):
        return False
    if any(p.get("env_key") and os.environ.get(p["env_key"])
           for p in PRESETS.values()):
        return False
    return True


def test_connection(provider_cfg: dict):
    """Returns (ok, message) after a small authenticated request; never raises."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=provider_cfg["api_key"],
                        base_url=provider_cfg["base_url"], timeout=25)
    except Exception as e:
        return False, str(e)[:300]
    try:
        client.chat.completions.create(
            model=provider_cfg["model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1)
        return True, "chat endpoint OK"
    except Exception as e1:
        try:
            client.models.list()
            return True, "models endpoint OK"
        except Exception:
            return False, str(e1)[:300]


def _ask_key(provider_name: str, env_key: str | None, stored_key: str | None = None):
    if not env_key:
        return None
    env_value = os.environ.get(env_key) or os.environ.get("NIJI_API_KEY")
    if env_value:
        use = Prompt.ask(f"Use existing {env_key if os.environ.get(env_key) else 'NIJI_API_KEY'} from environment?",
                         choices=["y", "n"], default="y")
        if use == "y":
            return env_value
    if stored_key:
        use = Prompt.ask(f"Keep the API key already saved for {provider_name}?",
                         choices=["y", "n"], default="y")
        if use == "y":
            return stored_key
    try:
        raw = getpass.getpass(f"Paste your {provider_name} API key (input hidden): ")
    except Exception:
        raw = input(f"Paste your {provider_name} API key: ")
    return raw.strip() or None


def _wizard_custom_provider():
    console.print("\n[bold]Custom provider[/] (OpenAI-compatible endpoint)")
    name = Prompt.ask("Provider name", default="custom").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", name):
        raise SystemExit("Use a provider name with letters, numbers, _ or - (max 32 chars).")
    base_url = Prompt.ask("Base URL", default="http://localhost:11434/v1").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SystemExit("Base URL must start with http:// or https:// and include a host.")
    model = Prompt.ask("Default model", default="default").strip()
    try:
        key = getpass.getpass("API key (Enter to skip): ").strip() or None
    except Exception:
        key = input("API key (Enter to skip): ").strip() or None
    return name, {"base_url": base_url, "model": model, "api_key": key}


def run_setup(default_model: str | None = None,
              provider_name: str | None = None) -> dict:
    from .config import PRESETS, load_config, resolve_provider, save_config

    console.print(Panel.fit(BANNER, border_style="cyan"))
    console.print("[bold]Welcome! Let's configure a provider.[/]\n")
    cfg = load_config()
    provider_names = [name for name, _ in WIZARD_PROVIDERS]
    configured_name = cfg.get("provider")
    default_name = (provider_name if provider_name in provider_names
                    else configured_name if configured_name in provider_names
                    else "openrouter")
    default_choice = str(provider_names.index(default_name) + 1)
    console.print("[bold]Choose a provider:[/]")
    for i, (_, label) in enumerate(WIZARD_PROVIDERS, 1):
        console.print(f"  {i}) {label}")
    console.print("  9) Custom (any OpenAI-compatible endpoint)")
    choice = Prompt.ask("> ", choices=[str(i) for i in range(1, 10)],
                        default=default_choice)

    if choice == "9":
        name, custom = _wizard_custom_provider()
        cfg.setdefault("custom_providers", {})[name] = custom
        cfg["provider"] = name
        save_config(cfg)
        provider_cfg = resolve_provider(name, model=custom["model"])
    else:
        name = provider_names[int(choice) - 1]
        preset = PRESETS[name]
        stored_key = cfg.get("api_keys", {}).get(name)
        key = _ask_key(name, preset.get("env_key"), stored_key)
        has_env_key = bool((preset.get("env_key") and os.environ.get(preset["env_key"]))
                           or os.environ.get("NIJI_API_KEY"))
        if preset.get("env_key") and not key and not stored_key and not has_env_key:
            raise SystemExit("No API key entered. Run 'niji setup' again when ready.")
        default_m = default_model or cfg.get("models", {}).get(name) or preset["model"]
        model = Prompt.ask("Default model", default=default_m).strip()
        if key:
            cfg.setdefault("api_keys", {})[name] = key
        cfg["provider"] = name
        cfg.setdefault("models", {})[name] = model
        save_config(cfg)
        provider_cfg = resolve_provider(name, model=model)

    with console.status("[cyan]Testing provider connection...[/]"):
        ok, msg = test_connection(provider_cfg)
    if ok:
        console.print(f"[green]✓ Connected[/] — {provider_cfg['provider']}/"
                      f"{provider_cfg['model']} ({msg})")
    else:
        console.print(f"[yellow]Provider saved; connection test failed:[/] {msg}")
        console.print("Check the key/model/network, then run: niji setup")
    return provider_cfg
