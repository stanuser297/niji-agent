"""Bounded, owner-only task plans stored separately from chat transcripts."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .config import SESSION_DIR

MAX_PLAN_ITEMS = 60
MAX_PLAN_TEXT = 500
_STATUSES = {"pending", "in_progress", "completed", "blocked"}
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def normalize_plan(items) -> list[dict[str, str]]:
    """Validate model-supplied steps and return a small, UI-safe representation."""
    if not isinstance(items, list) or len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"A plan must contain at most {MAX_PLAN_ITEMS} steps")
    result = []
    active = 0
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("Each plan step must be an object")
        content = item.get("content")
        status = item.get("status", "pending")
        active_form = item.get("activeForm", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Every plan step needs a description")
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError("Plan step status must be pending, in_progress, completed, or blocked")
        if not isinstance(active_form, str):
            active_form = ""
        if status == "in_progress":
            active += 1
        result.append({
            "id": str(item.get("id") or f"step-{index}")[:80],
            "content": content.strip()[:MAX_PLAN_TEXT],
            "status": status,
            "activeForm": active_form.strip()[:160],
        })
    if active > 1:
        raise ValueError("Only one plan step may be in progress")
    return result


def _path(session_id: str, root: str | Path | None = None) -> Path:
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValueError("Invalid session id for plan storage")
    return Path(root or SESSION_DIR) / "plans" / f"{session_id}.json"


def save_plan(session_id: str, items, root: str | Path | None = None) -> list[dict[str, str]]:
    """Atomically save one session's plan with owner-only filesystem permissions."""
    plan = normalize_plan(items)
    path = _path(session_id, root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=f".{session_id}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "session_id": session_id, "items": plan}, handle,
                      ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return plan


def load_plan(session_id: str, root: str | Path | None = None) -> list[dict[str, str]]:
    """Load a valid local plan; corrupted, unsafe, or missing state is treated as empty."""
    try:
        path = _path(session_id, root)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("session_id") != session_id:
            return []
        return normalize_plan(payload.get("items", []))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []


def extract_plan_steps(markdown: str) -> list[dict[str, str]]:
    """Extract numbered/bulleted steps from a tool-free plan response for UI display."""
    if not isinstance(markdown, str):
        return []
    markdown = markdown.replace("\\n", "\n")
    lines = markdown.splitlines()
    started = False
    items = []
    for line in lines:
        stripped = line.strip()
        heading = stripped.lstrip("#* ").rstrip(":* ").lower()
        is_section = (stripped.startswith("#") or
                      (stripped.endswith(":") and len(stripped) < 100))
        if not started and ("step" in heading or heading in {"plan", "approach", "proposed plan"}):
            started = True
            continue
        if started and is_section and any(
                key in heading for key in ("risk", "assumption", "verification", "check", "note")):
            break
        match = re.match(r"^(?:\d{1,2}[.)]|[-*])\s+(.+)$", stripped)
        if match and (started or re.match(r"^\d{1,2}[.)]", stripped)):
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            if value:
                items.append({"id": f"step-{len(items) + 1}", "content": value,
                              "status": "pending", "activeForm": ""})
                if len(items) >= MAX_PLAN_ITEMS:
                    break
        elif started and items and stripped and not stripped.startswith("#"):
            # Continue a wrapped description without swallowing another section.
            items[-1]["content"] = (items[-1]["content"] + " " + stripped)[:MAX_PLAN_TEXT]
    try:
        return normalize_plan(items)
    except ValueError:
        return []
