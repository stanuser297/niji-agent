"""Niji's responsive, uniquely branded Rich terminal dashboard."""
from pathlib import Path

from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__


def _compact_path(path: Path, limit: int) -> str:
    value = str(path)
    if len(value) <= limit:
        return value
    if limit < 8:
        return "…" + value[-max(1, limit - 1):]
    return "…" + value[-(limit - 1):]


def _brand_panel() -> Panel:
    mark = Text()
    mark.append("     ✦\n", style="bold bright_magenta")
    mark.append("   ╱ ▰ ▰ ▰ ╲\n", style="bold bright_cyan")
    mark.append("  ╱  ▰ ▰ ▰  ╲\n", style="bold bright_blue")
    mark.append("      ▰\n", style="bold yellow")
    mark.append("      ▰\n", style="bold bright_magenta")

    wordmark = Text("N I J I", style="bold bright_cyan")
    wordmark.append("  AGENT", style="bold white")
    copy = Text("PERSONAL AI WORKSPACE", style="bold bright_magenta")
    tagline = Text("Your ideas, in motion.", style="italic bright_yellow")
    release = Text(f"v{__version__}  •  OPENAI-COMPATIBLE", style="dim")
    content = Group(mark, wordmark, copy, tagline, release)
    return Panel(content, title="[bold bright_cyan]NIJI[/]  [dim]YOUR AI, YOUR WAY[/]",
                 border_style="bright_magenta", padding=(1, 2))


def _status_panel(agent, provider, width: int) -> Panel:
    rows = Table.grid(padding=(0, 1))
    rows.add_column(style="bold bright_cyan", no_wrap=True)
    rows.add_column(overflow="fold")
    rows.add_row("PROVIDER", Text(str(provider.get("provider", "unknown"))))
    rows.add_row("MODEL", Text(str(provider.get("model", "default"))))
    rows.add_row("WORKSPACE", Text(_compact_path(Path.cwd(), max(24, min(52, width // 2)))))
    rows.add_row("SESSION", Text(str(agent.session_id)[-18:]))
    mode = "CONFIRM ACTIONS" if getattr(agent, "approval", "auto") == "ask" else "AUTO"
    rows.add_row("MODE", Text(mode, style="bright_yellow" if mode == "AUTO" else "bright_green"))
    connectors = len(getattr(agent, "mcp_clients", []))
    rows.add_row("CONNECTORS", Text(f"{connectors} MCP connected"))
    return Panel(rows, title="[bold bright_yellow]LIVE SESSION[/]",
                 border_style="bright_blue", padding=(1, 2))


def render_home(agent, provider, quiet=False, console=None):
    """Render Niji's branded home screen, stacking panels on narrow terminals."""
    if quiet:
        return
    console = console or Console()
    width = console.size.width
    brand = _brand_panel()
    status = _status_panel(agent, provider, width)
    if width >= 100:
        console.print(Columns([brand, status], equal=True, expand=True, padding=(0, 1)))
    else:
        console.print(Group(brand, status))

    schemas = getattr(agent, "tool_schemas", [])
    names = [schema.get("function", {}).get("name", "tool") for schema in schemas]
    visible_count = 8 if width < 72 else 14
    shown = names[:visible_count]
    tools = Text("  ✦  ", style="bright_magenta")
    tools.append("  •  ".join(shown) if shown else "No tools loaded")
    if len(names) > len(shown):
        tools.append(f"  •  +{len(names) - len(shown)} more", style="dim bright_cyan")
    console.print(Panel(tools, title=f"[bold bright_cyan]READY[/]  [dim]{len(names)} active tools[/]",
                        border_style="cyan", padding=(0, 1)))
    hint = Text("Type a task naturally  ·  /help commands  ·  /setup provider  ·  /exit quit",
                style="dim")
    console.print(hint)
