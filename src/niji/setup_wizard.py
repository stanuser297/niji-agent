"""First-run setup wizard and provider connection diagnostics."""
import getpass
import os
import re
import warnings
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

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
    ("nvidia", "NVIDIA NIM (GLM 5.3 Flash and more)"),
]



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
            # Migrate a same-name custom provider through the preset wizard (e.g. NVIDIA).
            if cfg.get("custom_providers", {}).get(name):
                return True
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
    except Exception as e:
        # Listing models is not enough: validate the exact chat route and model.
        return False, str(e)[:300]


def _read_secret(label: str, allow_blank: bool = False):
    """Read a secret safely, explaining hidden paste and offering an opt-in fallback."""
    console.print("[dim]Key input is hidden; no letters or dots will appear. On Android, "
                  "long-press the terminal and choose Paste, then press Enter.[/]")
    warnings_seen = []
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", getpass.GetPassWarning)
            raw = getpass.getpass(label + ": ")
            warnings_seen = caught
    except Exception:
        raw = ""
    if warnings_seen:
        console.print("[yellow]This terminal cannot hide password input; it may have been echoed.[/]")
    raw = raw.strip()
    if raw:
        return raw
    if allow_blank:
        retry_text = "No key was received. If you meant to paste one, retry with visible input?"
    else:
        retry_text = "No key was received. Retry once with visible input?"
    retry = Prompt.ask(retry_text + " The key will show on screen", choices=["y", "n"], default="n")
    if retry == "y":
        return Prompt.ask("API key (visible on screen)").strip() or None
    return None


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
    return _read_secret(f"Paste your {provider_name} API key (hidden)")


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
    key = _read_secret("API key (optional for local/no-auth endpoints)", allow_blank=True)
    return name, {"base_url": base_url, "model": model, "api_key": key}


def _connection_guidance(provider_cfg: dict, message: str) -> str:
    """Turn common provider failures into actionable, provider-aware guidance."""
    text = message.lower()
    status = next((code for code in (401, 403, 404, 429) if str(code) in text), None)
    nvidia = "nvidia.com" in provider_cfg.get("base_url", "")
    if status in (401, 403):
        if nvidia:
            return ("NVIDIA denied this key or its access (HTTP %s). This is an authorization issue, "
                    "not a Niji branding problem; a wrong model usually returns 404. Choose `n` to "
                    "replace the saved key with an NVIDIA NIM API key, and check that your NVIDIA "
                    "account is allowed to use this model. The key is never shown on screen." % status)
        return (f"The provider denied this API key or its account access (HTTP {status}). "
                "Replace it with a key for this provider and confirm the account has API access.")
    if status == 404 and nvidia:
        return ("NVIDIA could not find this model or route (HTTP 404). For GLM 5.3 Flash the model "
                "ID is `z-ai/glm-5.3-flash` (dots, not hyphens).")
    if status == 404:
        return "The provider could not find this model or API route (HTTP 404). Check the base URL and exact model ID."
    if status == 429:
        return "The provider quota or rate limit was reached (HTTP 429). Check billing/quota and retry later."
    return "The connection test failed. Check network/DNS, API base URL, and model name."


def _read_replacement_key(provider_name: str) -> str:
    return _read_secret(f"Paste the replacement {provider_name} API key (hidden)") or ""


def run_setup(default_model: str | None = None,
              provider_name: str | None = None) -> dict:
    from .config import PRESETS, load_config, resolve_provider, save_config
    from .ui import render_setup_banner

    render_setup_banner(console)
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
    console.print("  10) Custom (any OpenAI-compatible endpoint)")
    choice = Prompt.ask("> ", choices=[str(i) for i in range(1, 11)],
                        default=default_choice)

    custom = None
    key = None
    if choice == "10":
        name, custom = _wizard_custom_provider()
        provider_cfg = {
            "provider": name,
            "base_url": custom["base_url"],
            "api_key": custom.get("api_key") or "custom",
            "model": custom["model"],
        }
    else:
        name = provider_names[int(choice) - 1]
        preset = PRESETS[name]
        stored_key = (cfg.get("api_keys", {}).get(name)
                      or cfg.get("custom_providers", {}).get(name, {}).get("api_key"))
        key = _ask_key(name, preset.get("env_key"), stored_key)
        has_env_key = bool((preset.get("env_key") and os.environ.get(preset["env_key"]))
                           or os.environ.get("NIJI_API_KEY"))
        if preset.get("env_key") and not key and not stored_key and not has_env_key:
            raise SystemExit("No API key entered. Run 'niji setup' again when ready.")
        default_m = default_model or cfg.get("models", {}).get(name) or preset["model"]
        model = Prompt.ask("Default model", default=default_m).strip()
        provider_cfg = resolve_provider(name, model=model, api_key=key)

    with console.status("[cyan]Testing provider connection...[/]"):
        ok, msg = test_connection(provider_cfg)

    if not ok and choice != "10" and preset.get("env_key") and any(
            code in msg for code in ("401", "403")):
        console.print(Panel(_connection_guidance(provider_cfg, msg),
                            title="[bold yellow]Key or provider access denied[/]",
                            border_style="yellow"))
        replace = Prompt.ask("Replace the saved key and test again? (input stays hidden)",
                             choices=["y", "n"], default="y")
        if replace == "y":
            replacement = _read_replacement_key(name)
            if replacement:
                key = replacement
                provider_cfg = resolve_provider(name, model=provider_cfg["model"],
                                                api_key=key)
                with console.status("[cyan]Retrying provider connection...[/]"):
                    ok, msg = test_connection(provider_cfg)

    if not ok:
        console.print(Panel(_connection_guidance(provider_cfg, msg),
                            title="[bold red]Niji setup needs attention[/]",
                            border_style="red"))
        console.print("Your existing saved settings were not overwritten by this failed test.")
        console.print("When ready, run: niji setup")
        raise SystemExit("Provider is not ready; chat was not started.")

    if custom is not None:
        cfg.setdefault("custom_providers", {})[name] = custom
        cfg["provider"] = name
    else:
        if key:
            cfg.setdefault("api_keys", {})[name] = key
        cfg.setdefault("custom_providers", {}).pop(name, None)
        cfg["provider"] = name
        cfg.setdefault("models", {})[name] = provider_cfg["model"]
    save_config(cfg)
    console.print(f"[green]✓ Connected[/] — {provider_cfg['provider']}/"
                  f"{provider_cfg['model']} ({msg})")
    return provider_cfg
