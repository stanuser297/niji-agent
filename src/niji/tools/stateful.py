def todo_read(ctx: dict) -> str:
    todos = (ctx.get("todos") or {}).get("items", [])
    if not todos:
        return "[no todos yet]"
    lines = []
    for i, t in enumerate(todos, 1):
        mark = {"pending": " ", "in_progress": ">", "completed": "x"}.get(t.get("status"), "?")
        lines.append(f"{mark} {i}. {t.get('content', '')}")
    return "\n".join(lines)


def todo_write(todos: list, activeForm: str = "", ctx: dict = None) -> str:
    if ctx is not None and "todos" in ctx:
        ctx["todos"]["items"] = todos
    return f"[ok] plan saved: {len(todos)} tasks ({activeForm or 'n/a'})"


def task(prompt: str, ctx: dict = None) -> str:
    """Spawn a subagent with a fresh context window."""
    from ..agent import Agent
    ctx = ctx or {}
    parent = ctx.get("agent")
    depth = ctx.get("depth", 0)
    if parent is None:
        return "[error] no parent agent"
    if depth >= 2:
        return "[error] subagent depth limit reached (max 2)"
    sub = Agent(
        provider_cfg=parent.provider_cfg,
        approval=parent.approval,
        max_turns=min(parent.max_turns, 30),
        verbose=False,
        depth=depth + 1,
        mcp_clients=[],
        allowed_tools=None,          # core tools minus task (see Agent)
    )
    result = sub.chat(prompt)
    return "[subagent report]\n" + str(result)[:12000]


def memory_read() -> str:
    from ..config import MEMORY_FILE
    if not MEMORY_FILE.exists():
        return "[memory empty]"
    return MEMORY_FILE.read_text(errors="replace")[:20000]


def memory_write(note: str) -> str:
    from ..config import MEMORY_FILE
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "a") as f:
        f.write(note.rstrip() + "\n")
    return "[ok] saved to long-term memory"
