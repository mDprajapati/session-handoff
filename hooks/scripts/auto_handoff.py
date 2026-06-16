#!/usr/bin/env python3
"""
Generic Auto-Handoff for Claude Code — works for ANY team or project type.
Sales, Marketing, SEO, Accounting, Development, HR, Legal, Design... all supported.

Fires on PreCompact (when context nears its limit) and writes HANDOFF.md
automatically into the current project directory. The companion SessionStart
hook (load_handoff.sh) injects that file back into the next session.
"""

import json
import sys
import os
import urllib.request
from datetime import datetime


# ─── Transcript Helpers ───────────────────────────────────────────────────────

def read_transcript(transcript_path):
    """Read and parse the JSONL session transcript."""
    messages = []
    if not transcript_path or not os.path.exists(transcript_path):
        return messages
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return messages


def extract_turns(messages):
    """Extract clean user/assistant turns from raw transcript messages."""
    turns = []
    for msg in messages:
        # Transcript entries may nest the actual message under "message"
        payload = msg.get("message", msg) if isinstance(msg, dict) else {}
        role = payload.get("role", "")
        content = payload.get("content", "")

        # content can be a string or a list of content blocks
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = " ".join(parts)

        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            turns.append({"role": role, "content": content.strip()})

    return turns


# ─── Summarization ────────────────────────────────────────────────────────────

HANDOFF_PROMPT = """You are creating a HANDOFF.md file for a work session that just hit its context limit.
This could be ANY kind of work — sales, marketing, SEO, accounting, development, research,
legal, HR, customer support, content writing, project planning, or anything else.

Your job is to:
1. Auto-detect what kind of work this session was doing.
2. Write a clear, structured handoff so work can resume seamlessly in a new session.

Here is the conversation from this session (most recent portion):
---
{conversation}
---

Write the HANDOFF.md using EXACTLY these sections.
Keep each section concise and specific — no fluff, no generic filler.

## Work Type
(One line: what kind of work is this? e.g. "Sales - Lead qualification for enterprise accounts"
or "SEO - On-page audit for client website" or "Marketing - Q3 email campaign planning")

## What Was Completed
(Bullet list of things finished or decided in this session)

## Current Status
(What is in-progress RIGHT NOW? What was the last action taken?)

## Pending Items
(Exact tasks still to do, in priority order)

## Important Context
(Critical information the next session MUST know:
- Names, companies, clients, contacts mentioned
- Numbers, dates, deadlines, budgets, targets
- Decisions made and why
- Constraints, rules, or preferences discovered
- Links, references, or document names mentioned)

## Watch Out For
(Any blockers, risks, open questions, or things that might go wrong)

## Resume Prompt
(Write ONE sentence the user can paste directly into the new session to resume.
Make it specific, not generic. Example: "Continue the SEO audit for acme.com -
we finished the title tags, next is meta descriptions starting from the blog section.")

Be specific. Be concise. Avoid vague statements like "continue the work" —
always say WHAT work, WHERE it was left, and WHAT comes next."""


def summarize_with_api(turns, api_key):
    """Use Claude Haiku to intelligently summarize the session."""
    if not api_key:
        return None

    # Use last 40 turns (balanced between context and cost)
    recent = turns[-40:]
    conversation = ""
    for t in recent:
        prefix = "USER" if t["role"] == "user" else "ASSISTANT"
        snippet = t["content"][:1000] if len(t["content"]) > 1000 else t["content"]
        conversation += f"\n\n{prefix}: {snippet}"

    prompt = HANDOFF_PROMPT.format(conversation=conversation.strip())

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

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
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]
    except Exception:
        return None


def build_fallback_summary(turns):
    """
    Fallback when the API is unavailable.
    Extracts raw content from the last messages — better than nothing.
    """
    lines = [
        "## Work Type",
        "*(Could not auto-detect — API unavailable)*\n",
        "## What Was Completed",
        "*(Review the last assistant messages below)*\n",
        "## Current Status",
    ]

    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    user_turns = [t for t in turns if t["role"] == "user"]

    if assistant_turns:
        last = assistant_turns[-1]["content"]
        snippet = last[:800] if len(last) > 800 else last
        lines.append(snippet)
    lines.append("")

    lines.append("## Pending Items")
    lines.append("*(Check the last user messages for pending requests)*\n")

    lines.append("## Important Context")
    if user_turns:
        lines.append("**Last user messages:**")
        for t in user_turns[-3:]:
            snippet = t["content"][:300] if len(t["content"]) > 300 else t["content"]
            lines.append(f"- {snippet}")
    lines.append("")

    lines.append("## Watch Out For")
    lines.append("*(Review the conversation manually for blockers)*\n")

    lines.append("## Resume Prompt")
    lines.append("Read HANDOFF.md and continue from where the last session stopped.\n")

    return "\n".join(lines)


# ─── File Writer ──────────────────────────────────────────────────────────────

def write_handoff_file(project_dir, body, trigger_reason):
    """Write the final HANDOFF.md to the project directory."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    trigger_label = {
        "auto":   "Auto-triggered — context window reached its limit",
        "manual": "Manually triggered via /compact",
    }.get(trigger_reason, "Context limit reached")

    header = f"""# Session Handoff

| Field | Value |
|-------|-------|
| **Generated** | {timestamp} |
| **Trigger** | {trigger_label} |
| **How to resume** | Start a new session, then paste the **Resume Prompt** below |

---

"""
    footer = """
---
> *Auto-generated by the session-handoff plugin (PreCompact hook).*
> *Delete this file after resuming to keep your project clean.*
"""

    full_content = header + body + footer
    handoff_path = os.path.join(project_dir, "HANDOFF.md")

    with open(handoff_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return handoff_path


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Read hook input (PreCompact passes session info via stdin)
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        hook_input = {}

    transcript_path = hook_input.get("transcript_path", "")
    trigger_reason  = hook_input.get("trigger", "auto")
    project_dir     = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    api_key         = os.environ.get("ANTHROPIC_API_KEY", "")

    # Parse transcript
    raw_messages = read_transcript(transcript_path)
    turns = extract_turns(raw_messages)

    if not turns:
        # Nothing to summarize — exit silently
        sys.exit(0)

    # Generate summary, fall back to manual extraction if the API is unavailable
    summary = summarize_with_api(turns, api_key)
    if not summary:
        summary = build_fallback_summary(turns)

    # Write HANDOFF.md
    handoff_path = write_handoff_file(project_dir, summary, trigger_reason)

    # Inject a notice into Claude's context so it knows what happened
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": (
                f"HANDOFF.md has been saved to: {handoff_path}\n"
                "The next session will automatically load this file and resume your work."
            ),
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
