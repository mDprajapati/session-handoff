# Session Handoff

Never lose your place when a Claude Code session hits its context limit. This
plugin automatically captures everything you were working on into a `HANDOFF.md`
file the moment context fills up, then automatically loads that file back into your
next session so you can resume instantly — no re-explaining from scratch.

It is **fully generic**: it auto-detects the kind of work from the conversation, so
it works the same for development, sales, marketing, SEO, accounting, HR, legal,
research, support, content, or planning teams.

## What It Does

1. **At ~90% context** — the `PreCompact` hook fires, reads the session transcript,
   and uses the Anthropic API to write a structured `HANDOFF.md` in your project
   folder (work type, what's done, current status, pending items, key context,
   blockers, and a ready-to-paste resume prompt).
2. **At the start of a new session** — the `SessionStart` hook detects `HANDOFF.md`
   and injects it back into context, so Claude already knows where to pick up.
3. **On demand** — the bundled skill lets you say "save my progress" or "create a
   handoff" any time, without waiting for the limit.

## Components

| Component | Purpose |
|-----------|---------|
| `PreCompact` hook (`auto_handoff.py`) | Generates `HANDOFF.md` when context nears the limit |
| `SessionStart` hook (`load_handoff.sh`) | Loads `HANDOFF.md` into the next session |
| `session-handoff` skill | Guides handoff creation, what-to-preserve rules, and resume behavior |

## Setup

For the AI-generated summary, set your Anthropic API key in the environment:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

If no key is present, the plugin still works — it falls back to extracting the last
messages of the session directly, so you never lose your handoff entirely.

No other configuration is required. After installing, restart Claude Code so the
hooks activate.

## Usage

- **Automatic:** Just work normally. When context fills up, `HANDOFF.md` appears in
  your project folder and is loaded for you next time.
- **Manual:** Ask Claude to "create a handoff" or "save my progress" at any point.
- **Test it:** Run `/compact` inside Claude Code, then check your project folder for
  `HANDOFF.md`.

## Notes

- `HANDOFF.md` is written to the current project directory (`CLAUDE_PROJECT_DIR`).
- Delete `HANDOFF.md` after you've resumed to keep the project clean.
- The exact trigger point is whenever Claude Code decides to compact (typically
  80–95% context); `PreCompact` is the closest built-in hook to a strict "90%".
