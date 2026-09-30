import os
import subprocess
from pathlib import Path

MAX_OUTPUT = 20000

def _truncate(s: str) -> str:
    if len(s) <= MAX_OUTPUT:
        return s
    return s[:MAX_OUTPUT] + f"\n... [truncated {len(s) - MAX_OUTPUT} chars]"


def bash(command: str, cwd: str | None = None, timeout: int = 120) -> str:
    from ..safety import check_command, subprocess_environment
    check_command(command)
    try:
        timeout = max(1, min(int(timeout), 120))
        p = subprocess.run(command, shell=True, cwd=cwd or os.getcwd(),
                           capture_output=True, text=True, timeout=timeout,
                           env=subprocess_environment())
        out = _truncate(((p.stdout or "") + (p.stderr or "")).strip())
        return out or f"[exit code {p.returncode}, no output]"
    except subprocess.TimeoutExpired:
        return f"[error] timed out after {timeout}s"
    except Exception as e:
        return f"[error] {e}"


def read_file(path: str, offset: int = 0, limit: int = 400) -> str:
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 1000))
    p = Path(path)
    if not p.is_file():
        return f"[error] not a file: {path}"
    try:
        lines = p.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"[error] {e}"
    chunk = lines[offset:offset + limit]
    header = f"# {path} (lines {offset + 1}-{offset + len(chunk)} of {len(lines)})"
    return header + "\n" + "\n".join(chunk)


def _snapshot_before(p: Path):
    """Return (snapshot, reversible); cap in-memory undo snapshots at 1 MiB."""
    if not p.exists():
        return None, True
    if not p.is_file():
        return None, False
    try:
        if p.stat().st_size > 1_000_000:
            return None, False
        return p.read_bytes(), True
    except OSError:
        return None, False


def _record_undo(ctx, p: Path, before, after: bytes, operation, reversible):
    agent = (ctx or {}).get("agent")
    if not reversible or agent is None:
        return False
    return agent.record_file_change(p, before, after, operation)


def write_file(path: str, content: str, ctx: dict = None) -> str:
    p = Path(path)
    existed = p.exists()
    before, reversible = _snapshot_before(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    recorded = _record_undo(ctx, p, before if existed else None,
                            content.encode("utf-8"), "write_file", reversible)
    message = f"[ok] wrote {len(content)} chars to {path}"
    if recorded:
        message += " (undo checkpoint available with /undo)"
    elif existed and not reversible:
        message += " (file exceeded the 1 MB undo-snapshot limit)"
    return message


def edit_file(path: str, old_text: str, new_text: str, ctx: dict = None) -> str:
    p = Path(path)
    if not p.is_file():
        return f"[error] not a file: {path}"
    before, reversible = _snapshot_before(p)
    text = p.read_text(errors="replace")
    n = text.count(old_text)
    if n == 0:
        return "[error] old_text not found in file"
    if n > 1:
        return f"[error] old_text found {n} times — must be unique. Give more context."
    updated = text.replace(old_text, new_text, 1)
    p.write_text(updated)
    recorded = _record_undo(ctx, p, before, updated.encode("utf-8"), "edit_file", reversible)
    message = "[ok] edit applied"
    if recorded:
        message += " (undo checkpoint available with /undo)"
    elif not reversible:
        message += " (file exceeded the 1 MB undo-snapshot limit)"
    return message


def list_files(path: str = ".", depth: int = 2) -> str:
    base = Path(path)
    if not base.is_dir():
        return f"[error] not a directory: {path}"
    out = []
    for p in sorted(base.rglob("*")):
        if len(p.parts) - len(base.parts) > depth:
            continue
        out.append(str(p))
        if len(out) >= 500:
            out.append("... [truncated at 500 entries]")
            break
    return "\n".join(out) or "[empty]"


def grep(pattern: str, path: str = ".", include: str = "*") -> str:
    import re
    base = Path(path)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"[error] bad regex: {e}"
    matches = []
    for p in base.rglob(include):
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{p}:{i}: {line.strip()[:200]}")
                    if len(matches) >= 200:
                        return "\n".join(matches) + "\n... [truncated at 200 matches]"
        except Exception:
            continue
    return "\n".join(matches) or "[no matches]"


def glob(pattern: str, path: str = ".") -> str:
    base = Path(path)
    hits = [str(p) for p in base.rglob(pattern) if p.is_file()]
    return "\n".join(hits[:200]) or "[no matches]"


def web_fetch(url: str, max_chars: int = 15000) -> str:
    max_chars = max(500, min(int(max_chars), 15000))
    import html
    import ipaddress
    import re
    import urllib.request
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "[error] only http:// and https:// URLs are allowed"
    if parsed.username or parsed.password:
        return "[error] URLs containing embedded credentials are not allowed"
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return "[error] local/private hosts are not allowed"
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            return "[error] local/private IP addresses are not allowed"
    except ValueError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": "niji-agent/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(500_000)
            ctype = r.headers.get("Content-Type", "")
    except Exception as e:
        return f"[error] fetch failed: {e}"
    text = raw.decode("utf-8", errors="replace")
    if "html" in ctype or "<html" in text[:1000].lower():
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)
    return _truncate(text.strip()[:max_chars])


def read_image(path: str):
    import base64
    import mimetypes
    p = Path(path)
    if not p.is_file():
        return f"[error] not a file: {path}"
    if p.stat().st_size > 10_000_000:
        return "[error] image too large (>10MB)"
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    if not mime.startswith("image/"):
        return f"[error] not an image: {path}"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return [{"type": "text", "text": f"[image: {path}]"},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{b64}"}}]
