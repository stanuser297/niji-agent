from .builtin import (bash, read_file, write_file, edit_file, list_files,
                      grep, glob, web_fetch, read_image)
from .stateful import todo_read, todo_write, task, memory_read, memory_write

HANDLERS = {
    "bash": bash, "read_file": read_file, "write_file": write_file,
    "edit_file": edit_file, "list_files": list_files, "grep": grep, "glob": glob,
    "web_fetch": web_fetch, "read_image": read_image,
    "todo_read": todo_read, "todo_write": todo_write,
    "memory_read": memory_read, "memory_write": memory_write,
    "task": task,
}

# tools a subagent is allowed to use ("task" excluded to stop recursion)
SUBAGENT_TOOLS = [k for k in HANDLERS if k != "task"]


def dispatch(name: str, args: dict, ctx: dict = None):
    ctx = ctx or {}
    fn = HANDLERS.get(name)
    if fn:
        if name == "todo_read":
            return fn(ctx)
        if name in ("todo_write", "task", "write_file", "edit_file"):
            return fn(ctx=ctx, **args)
        return fn(**args)
    # MCP connector tools: "<server>__<tool>"
    if "__" in name:
        server, tool = name.split("__", 1)
        client = (ctx.get("mcp") or {}).get(server)
        if client:
            try:
                return client.call(tool, args)
            except Exception as e:
                return f"[connector error] {e}"
    return f"[error] unknown tool: {name}"


def _schema(name, desc, props, req):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": req}}}

def _s(t, d, **kw):
    return {"type": t, "description": d, **kw}


CORE_SCHEMAS = [
    _schema("bash",
            "Run a shell command (build, test, git, install, execute code, run scripts). "
            "Returns combined stdout+stderr.",
            {"command": _s("string", "The shell command"),
             "cwd": _s("string", "Working directory (optional)"),
             "timeout": _s("integer", "Timeout seconds (default 120)")},
            ["command"]),
    _schema("read_file", "Read a text file with line numbers.",
            {"path": _s("string", "File path"),
             "offset": _s("integer", "Start line (0-based)"),
             "limit": _s("integer", "Max lines (default 400)")},
            ["path"]),
    _schema("write_file", "Create or overwrite a file with exact content. Niji keeps a private, session-local undo checkpoint for files up to 1 MB; the user can restore it with /undo.",
            {"path": _s("string", "File path"),
             "content": _s("string", "Full file content")},
            ["path", "content"]),
    _schema("edit_file",
            "Replace exactly ONE unique occurrence of old_text with new_text. "
            "Prefer over write_file for small changes. Niji keeps a private, session-local undo checkpoint for files up to 1 MB; the user can restore it with /undo.",
            {"path": _s("string", "File path"),
             "old_text": _s("string", "Exact unique text to replace"),
             "new_text": _s("string", "Replacement text")},
            ["path", "old_text", "new_text"]),
    _schema("list_files", "List files in a directory tree up to a depth.",
            {"path": _s("string", "Directory (default .)"),
             "depth": _s("integer", "Max depth (default 2)")},
            []),
    _schema("grep", "Regex-search file contents under a directory.",
            {"pattern": _s("string", "Regex pattern"),
             "path": _s("string", "Directory (default .)"),
             "include": _s("string", "Glob filter e.g. *.py (default *)")},
            ["pattern"]),
    _schema("glob", "Find files by glob pattern.",
            {"pattern": _s("string", "Glob e.g. **/*.py"),
             "path": _s("string", "Base directory (default .)")},
            ["pattern"]),
    _schema("web_fetch",
            "Fetch a URL and return its text content (HTML stripped). "
            "Use for docs, APIs, pages.",
            {"url": _s("string", "Full URL including https://"),
             "max_chars": _s("integer", "Max chars to return (default 15000)")},
            ["url"]),
    _schema("read_image",
            "Read an image file so a vision model can see it (screenshots, diagrams, photos).",
            {"path": _s("string", "Image file path")},
            ["path"]),
    _schema("todo_write",
            "Plan and track progress on multi-step tasks. Overwrite the full list each time. "
            "Mark exactly one task in_progress.",
            {"todos": _s("array", "The task list", items={
                "type": "object",
                "properties": {
                    "content": _s("string", "What to do"),
                    "status": _s("string", "pending | in_progress | completed",
                                 enum=["pending", "in_progress", "completed"]),
                    "activeForm": _s("string", "Short form shown while working on it"),
                },
                "required": ["content", "status"]}),
             "activeForm": _s("string", "What you are doing right now")},
            ["todos"]),
    _schema("todo_read", "Read the current task plan.", {}, []),
    _schema("task",
            "Launch a SUBAGENT with a fresh context window to handle a self-contained "
            "subtask (research, a focused bug, exploring a big file). The subagent has all "
            "tools but cannot spawn more subagents. Returns its final report.",
            {"prompt": _s("string", "Complete, self-contained instructions for the subagent")},
            ["prompt"]),
    _schema("memory_read",
            "Read niji's long-term memory (persistent across all sessions/projects).",
            {}, []),
    _schema("memory_write",
            "Append a fact/preference/decision to long-term memory. Use sparingly, "
            "for things useful across sessions (user prefs, project conventions).",
            {"note": _s("string", "The fact to remember, 1-3 lines")},
            ["note"]),
]
