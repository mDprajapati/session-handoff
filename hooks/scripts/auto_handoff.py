#!/usr/bin/env python3
"""
Auto-Handoff for Claude Code — works for ANY team or project type.
Sales, Marketing, SEO, Accounting, Development, HR, Legal, Design... all supported.

Fires on PreCompact (when context nears its limit) and writes a structured
handoff into the git-ignored .session-handoff/ directory in the current project.
The companion SessionStart hook (load_handoff.py) injects that file back into the
next session.

Design goals for team use:
  - Never pollute the repo: output lives in .session-handoff/, which is added to
    .gitignore automatically.
  - Never lose history: each handoff is also kept as a timestamped file.
  - Never leak secrets: the transcript is redacted before it is sent to the API
    or written to disk, and a local-only mode skips the API entirely.
  - Never hang the session: the API call has a short timeout and falls through to
    a local summary.
  - Never fail silently: every step is logged to .session-handoff/handoff.log.

Standard library only — no third-party dependencies.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

# Import the shared library that sits next to this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_lib as lib  # noqa: E402

# Force UTF-8 on stdout so emitting the JSON notice can't crash on a Windows
# console whose default code page (e.g. cp1252) can't encode non-ASCII text.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ─── Transcript Helpers ───────────────────────────────────────────────────────

def read_transcript(transcript_path):
    """Read and parse the JSONL session transcript. Never raises."""
    messages = []
    if not transcript_path or not os.path.exists(transcript_path):
        return messages
    try:
        # utf-8-sig transparently strips a leading BOM if one is present, so a
        # BOM-prefixed transcript can't silently drop its first message.
        with open(transcript_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip malformed lines rather than aborting the whole parse.
                    continue
    except Exception:
        pass
    return messages


def _summarize_tool_use(block):
    """Render a tool_use content block as a short, human-readable trace line."""
    name = block.get("name", "tool")
    tool_input = block.get("input", {}) or {}

    # Surface the most informative argument for common tools without dumping
    # full payloads (which would be noisy and could contain secrets).
    for key in ("file_path", "path", "command", "pattern", "query", "url", "notebook_path"):
        if key in tool_input and tool_input[key]:
            value = str(tool_input[key])
            value = value if len(value) <= 200 else value[:200] + "…"
            return f"{name}({key}={value})"
    return f"{name}(…)"


def extract_turns(messages):
    """
    Extract clean user/assistant turns from raw transcript messages.

    Unlike a text-only extractor, this also captures a compact trace of tool
    activity (file edits, commands, searches) because for development work the
    real state lives in tool calls, not prose.
    """
    turns = []
    for msg in messages:
        payload = msg.get("message", msg) if isinstance(msg, dict) else {}
        role = payload.get("role", "")
        content = payload.get("content", "")

        text_parts = []
        tool_parts = []

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_parts.append(_summarize_tool_use(block))
                elif btype == "tool_result":
                    # Record only that a result returned and whether it errored.
                    if block.get("is_error"):
                        tool_parts.append("→ tool_result: ERROR")
        elif isinstance(content, str):
            text_parts.append(content)

        text = " ".join(p for p in text_parts if p).strip()

        if role in ("user", "assistant") and (text or tool_parts):
            turns.append({
                "role": role,
                "content": text,
                "tools": tool_parts,
            })

    return turns


def build_activity_trace(turns, limit=25):
    """Collect the most recent tool actions into a compact, ordered list."""
    actions = []
    for t in turns:
        for tool in t.get("tools", []):
            actions.append(tool)
    return actions[-limit:]


# ─── Summarization ────────────────────────────────────────────────────────────

def build_prompt(turns, config):
    """Assemble the summarizer prompt from the shared section schema."""
    recent = turns[-config["max_turns"]:]
    cap = config["max_snippet_chars"]

    conversation = ""
    for t in recent:
        prefix = "USER" if t["role"] == "user" else "ASSISTANT"
        snippet = t["content"][:cap] if len(t["content"]) > cap else t["content"]
        line = f"\n\n{prefix}: {snippet}".rstrip()
        if t.get("tools"):
            line += "\n  [actions: " + "; ".join(t["tools"]) + "]"
        conversation += line

    trace = build_activity_trace(turns)
    trace_block = ""
    if trace:
        trace_block = "\n\nRecent tool activity (most recent last):\n- " + "\n- ".join(trace)

    return (
        "You are creating a session handoff for a work session that just hit its "
        "context limit. This could be ANY kind of work — sales, marketing, SEO, "
        "accounting, development, research, legal, HR, support, content, or "
        "planning. First auto-detect the kind of work, then write a clear, "
        "structured handoff so work can resume seamlessly in a new session.\n\n"
        "Here is the conversation from this session (most recent portion):\n"
        f"---{conversation}{trace_block}\n---\n\n"
        "Write the handoff using EXACTLY these sections. Keep each section "
        "concise and specific — no fluff, no generic filler. Avoid vague "
        "statements like \"continue the work\": always say WHAT work, WHERE it "
        "was left, and WHAT comes next.\n\n"
        f"{lib.sections_for_prompt()}"
    )


def summarize_with_api(turns, api_key, config, project_dir):
    """Use the configured Claude model to summarize. Returns text or None."""
    if config["local_only"]:
        lib.log(project_dir, "Local-only mode enabled — skipping API summary.")
        return None
    if not api_key:
        lib.log(project_dir, "No ANTHROPIC_API_KEY — falling back to local summary.")
        return None

    prompt = build_prompt(turns, config)
    payload = json.dumps({
        "model": config["model"],
        "max_tokens": config["max_tokens"],
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    last_error = None
    for attempt in range(1, config["api_attempts"] + 1):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config["api_timeout"]) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = result["content"][0]["text"]
                lib.log(project_dir,
                        f"API summary OK (model={config['model']}, attempt={attempt}).")
                return text
        except Exception as exc:  # noqa: BLE001 — log and fall through to local.
            last_error = exc
            lib.log(project_dir,
                    f"API summary attempt {attempt}/{config['api_attempts']} "
                    f"failed: {type(exc).__name__}: {exc}")

    lib.log(project_dir, f"All API attempts failed; using local summary. Last: {last_error}")
    return None


def _last_concrete_user_request(turns):
    """Find the most recent non-trivial user message for the resume line."""
    for t in reversed(turns):
        if t["role"] != "user":
            continue
        text = t["content"].strip()
        # Skip empty or trivial acknowledgements.
        if len(text) < 8:
            continue
        if text.lower() in ("ok", "okay", "thanks", "thank you", "yes", "no", "continue"):
            continue
        return text
    return None


def build_fallback_summary(turns):
    """
    Local summary used when the API is unavailable or disabled.

    Unlike a generic placeholder, this extracts the last concrete user request so
    the Resume Prompt is actually actionable — the SKILL forbids vague filler.
    """
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    last_request = _last_concrete_user_request(turns)
    trace = build_activity_trace(turns)

    lines = [
        "## Work Type",
        "*(Auto-detection unavailable — generated locally without the API.)*\n",
        "## What Was Completed",
        "*(Review the recent activity and last assistant message below.)*\n",
        "## Current Status",
    ]

    if assistant_turns:
        last = assistant_turns[-1]["content"]
        lines.append(last[:800] if len(last) > 800 else last)
    else:
        lines.append("*(No assistant messages captured.)*")
    lines.append("")

    lines.append("## Pending Items")
    if last_request:
        lines.append(f"- Address the last user request: {last_request[:300]}")
    else:
        lines.append("*(Check the recent user messages for pending requests.)*")
    lines.append("")

    lines.append("## Important Context")
    user_turns = [t for t in turns if t["role"] == "user"]
    if user_turns:
        lines.append("**Recent user messages:**")
        for t in user_turns[-3:]:
            snippet = t["content"][:300] if len(t["content"]) > 300 else t["content"]
            if snippet:
                lines.append(f"- {snippet}")
    lines.append("")

    lines.append("## Activity Trace")
    if trace:
        for action in trace:
            lines.append(f"- {action}")
    else:
        lines.append("*(No tool activity captured.)*")
    lines.append("")

    lines.append("## Watch Out For")
    lines.append("*(API was unavailable — review the conversation manually for blockers.)*\n")

    lines.append("## Resume Prompt")
    if last_request:
        lines.append(f"Resume the previous session: {last_request[:300]}")
    else:
        lines.append(
            "Open .session-handoff/HANDOFF.md and resume the most recent task "
            "described in the recent user messages above."
        )
    lines.append("")

    return "\n".join(lines)


# ─── Repo hygiene ───────────────────────────────────────────────────────────────

def ensure_gitignored(project_dir):
    """
    Make sure .session-handoff/ is git-ignored so handoffs never get committed.

    Only touches .gitignore inside a git repo, and only appends if the entry is
    not already present. Best-effort — never raises.
    """
    try:
        if not os.path.isdir(os.path.join(project_dir, ".git")):
            return  # Not a git repo (or a worktree/submodule) — leave it alone.

        gitignore = os.path.join(project_dir, ".gitignore")
        entry = lib.HANDOFF_DIR_NAME + "/"

        existing = ""
        if os.path.exists(gitignore):
            with open(gitignore, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()

        # Match the directory whether or not it has a trailing slash.
        present = any(
            line.strip().rstrip("/") == lib.HANDOFF_DIR_NAME
            for line in existing.splitlines()
        )
        if present:
            return

        prefix = "" if existing.endswith("\n") or existing == "" else "\n"
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write(f"{prefix}\n# Added by session-handoff plugin\n{entry}\n")
        lib.log(project_dir, "Added .session-handoff/ to .gitignore.")
    except Exception as exc:  # noqa: BLE001
        lib.log(project_dir, f"Could not update .gitignore: {exc}")


# ─── File Writer ──────────────────────────────────────────────────────────────

def _prune_history(project_dir, keep):
    """Keep only the most recent `keep` timestamped history files."""
    try:
        hdir = lib.history_dir(project_dir)
        files = sorted(
            (f for f in os.listdir(hdir) if f.startswith("HANDOFF-") and f.endswith(".md"))
        )
        for stale in files[:-keep] if keep > 0 else files:
            try:
                os.remove(os.path.join(hdir, stale))
            except OSError:
                pass
    except Exception:
        pass


def write_handoff(project_dir, body, trigger_reason, config):
    """
    Write the handoff to .session-handoff/, both as the 'latest' pointer and as a
    timestamped history file. Returns the path to the latest file.
    """
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    stamp_compact = now.strftime("%Y%m%d-%H%M%S")

    trigger_label = {
        "auto": "Auto-triggered — context window reached its limit",
        "manual": "Manually triggered via /compact",
    }.get(trigger_reason, "Context limit reached")

    header = (
        f"{lib.generated_at_marker()}\n"
        "# Session Handoff\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"| **Generated** | {timestamp} |\n"
        f"| **Trigger** | {trigger_label} |\n"
        "| **How to resume** | Start a new session, then paste the **Resume Prompt** below |\n\n"
        "---\n\n"
    )
    footer = (
        "\n---\n"
        "> *Auto-generated by the session-handoff plugin (PreCompact hook).*\n"
        "> *History is kept under `.session-handoff/history/`.*\n"
    )

    full_content = header + body + footer

    # Final safety pass: redact secrets in whatever we're about to persist.
    if config["redact_secrets"]:
        full_content = lib.redact(full_content)

    os.makedirs(lib.history_dir(project_dir), exist_ok=True)

    latest = lib.latest_path(project_dir)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(full_content)

    history_file = os.path.join(lib.history_dir(project_dir), f"HANDOFF-{stamp_compact}.md")
    with open(history_file, "w", encoding="utf-8") as f:
        f.write(full_content)

    _prune_history(project_dir, config["history_keep"])
    return latest


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        hook_input = {}

    transcript_path = hook_input.get("transcript_path", "")
    trigger_reason = hook_input.get("trigger", "auto")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    config = lib.load_config(project_dir)
    lib.log(project_dir, f"PreCompact fired (trigger={trigger_reason}).")

    raw_messages = read_transcript(transcript_path)
    turns = extract_turns(raw_messages)
    lib.log(project_dir, f"Parsed transcript: {len(raw_messages)} messages, {len(turns)} turns.")

    if not turns:
        lib.log(project_dir, "No turns to summarize - exiting.")
        sys.exit(0)

    # Redact the transcript BEFORE it is sent anywhere.
    if config["redact_secrets"]:
        for t in turns:
            t["content"] = lib.redact(t["content"])

    summary = summarize_with_api(turns, api_key, config, project_dir)
    used_api = summary is not None
    if not summary:
        summary = build_fallback_summary(turns)

    ensure_gitignored(project_dir)

    try:
        handoff_path = write_handoff(project_dir, summary, trigger_reason, config)
    except Exception as exc:  # noqa: BLE001
        lib.log(project_dir, f"FAILED to write handoff: {type(exc).__name__}: {exc}")
        sys.exit(0)  # Don't break compaction even if the write fails.

    lib.log(project_dir, f"Handoff written to {handoff_path} (api={used_api}).")

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": (
                f"A session handoff has been saved to: {handoff_path}\n"
                "The next session will automatically load it and resume your work."
            ),
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
