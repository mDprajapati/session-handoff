---
name: session-handoff
description: >
  This skill should be used when context is being compacted, when the user asks
  to "save my progress", "create a handoff", "write a HANDOFF.md", "summarize
  what we did so we can continue later", or when a session is approaching its
  context limit and work needs to carry over into a new session. Applies to any
  kind of work — sales, marketing, SEO, accounting, development, research, legal,
  HR, support, content, or planning.
metadata:
  version: "0.2.1"
---

# Session Handoff

Preserve the state of in-progress work so it can resume cleanly in a new session
after a context limit, compaction, or deliberate restart. This works for ANY kind
of work, not just coding.

## When This Applies

Engage this skill whenever:

- Context is being compacted (the `PreCompact` hook fires automatically).
- The user explicitly asks to save progress, hand off, or summarize for later.
- A long task is clearly approaching the point where a new session will be needed.

## What To Always Preserve

When compacting or summarizing, never drop the following. These are the items the
next session needs to continue without re-explaining anything:

- The main goal or task of the current session.
- What has been completed and what is still pending.
- Names of people, companies, clients, or tools mentioned.
- Important numbers: dates, budgets, targets, quantities, IDs.
- Decisions that were made, and the reason behind each one.
- The single most important next action to take.
- Any blockers, open questions, or risks that were flagged.

## Writing a Handoff Manually

If asked to create a handoff (rather than waiting for the automatic hook), write the
file to `.session-handoff/HANDOFF.md` in the project directory (the git-ignored
location the loader reads). Use exactly these sections:

1. **Work Type** — one line auto-detecting the kind of work (e.g. "SEO - On-page
   audit for client website").
2. **What Was Completed** — bullet list of finished or decided items.
3. **Current Status** — what is in-progress right now; the last action taken.
4. **Pending Items** — exact remaining tasks, in priority order.
5. **Important Context** — names, numbers, deadlines, decisions, constraints, links.
6. **Activity Trace** — for technical work, a compact list of concrete actions
   (files edited, commands run, tests and their outcome). Omit if not applicable.
7. **Watch Out For** — blockers, risks, open questions.
8. **Resume Prompt** — one specific sentence the user can paste into a new session
   to resume immediately. Never vague: always state WHAT work, WHERE it was left,
   and WHAT comes next.

> The canonical section schema lives in `hooks/scripts/handoff_lib.py` (`SECTIONS`)
> so the automatic hook and this skill stay in sync. If you change the sections,
> change them there too.

## Resuming From a Handoff

At the start of a session, the `SessionStart` hook prints any existing
`.session-handoff/HANDOFF.md`. When that content is present:

- Treat the **Resume Prompt** and **Pending Items** as the immediate to-do list.
- If the loader flagged the handoff as **POSSIBLY STALE**, confirm with the user
  that it is still relevant before resuming — it may belong to a different task.
- Confirm the resumed task back to the user in one line before continuing.
- Once the handed-off work is complete, remind the user they can delete
  `.session-handoff/HANDOFF.md` so it does not load again next session.

## Style Rules

- Be specific and concise. Avoid filler like "continue the work" with no detail.
- Auto-detect the work type from the conversation — do not assume it is a coding task.
- Keep the handoff scannable: short bullets, concrete facts, no narration.
