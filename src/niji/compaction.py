import json


def estimate_tokens(messages) -> int:
    total = 0
    for m in messages:
        total += len(str(m.get("content") or ""))
        for tc in (m.get("tool_calls") or []):
            total += len(json.dumps(tc))
    return total // 4


def maybe_compact(messages, client, model, max_tokens=60000, keep_recent=14, force=False):
    if not force and (estimate_tokens(messages) <= max_tokens
                      or len(messages) < keep_recent + 4):
        return messages, False

    system = messages[0]
    dropped = messages[1:-keep_recent] if len(messages) > keep_recent else []
    tail = messages[-keep_recent:] if dropped else messages[1:]

    transcript = [json.dumps(m)[:3000] for m in dropped]
    prompt = (
        "You are maintaining context for a coding agent. Summarize the conversation "
        "below so the agent can continue its task. Preserve: file paths being edited, "
        "commands already run and their results, decisions made, and what remains to do. "
        "Be dense and factual.\n\n" + "\n".join(transcript)
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        summary = resp.choices[0].message.content
    except Exception:
        summary = "[earlier context was summarized but the summary call failed]"

    compacted = [
        system,
        {"role": "user",
         "content": f"[Summary of earlier work]\n{summary}\n[Resume the task from here.]"},
        *tail,
    ]
    return compacted, True
