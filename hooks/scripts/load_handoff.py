#!/usr/bin/env python3
"""
Session loader (SessionStart hook) — cross-platform replacement for the old
bash-only loader, so it runs identically on Windows, macOS, and Linux.

Injects the most recent handoff into a new session, but flags it as POSSIBLY
STALE if it is older than the configured threshold — a weeks-old handoff from a
different task should not be presented as the live to-do list.

Prints to stdout, which Claude Code injects into the new session's context.
Standard library only.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handoff_lib as lib  # noqa: E402

# Force UTF-8 on stdout. The handoff content can contain non-ASCII characters
# (em-dashes, accented names, etc.); without this, printing it crashes on a
# Windows console whose default code page is cp1252 — and because stdout is
# block-buffered when piped, the crash would discard everything, including the
# staleness warning printed first.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _format_age(delta):
    """Human-readable age like '3 hours' or '2 days'."""
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''}"


def main():
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    config = lib.load_config(project_dir)

    handoff_file = lib.latest_path(project_dir)
    if not os.path.exists(handoff_file):
        # Nothing to resume — stay silent so normal sessions aren't cluttered.
        return

    try:
        with open(handoff_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as exc:  # noqa: BLE001
        lib.log(project_dir, f"Loader could not read handoff: {exc}")
        return

    # Decide whether the handoff is fresh or possibly stale.
    generated = lib.parse_generated_at(content)
    stale_note = ""
    age_line = ""
    if generated is not None:
        now = datetime.now(timezone.utc).astimezone()
        age = now - generated
        age_line = f"Generated {_format_age(age)} ago."
        if age.total_seconds() > config["stale_hours"] * 3600:
            stale_note = (
                f"\n!! POSSIBLY STALE: this handoff is {_format_age(age)} old "
                f"(older than the {config['stale_hours']}h threshold). It may belong "
                "to a different task. Confirm with the user before resuming, and "
                "delete .session-handoff/HANDOFF.md if it is no longer relevant.\n"
            )

    print("=" * 46)
    print("        PREVIOUS SESSION HANDOFF FOUND        ")
    print("=" * 46)
    if age_line:
        print(age_line)
    if stale_note:
        print(stale_note)
    print()
    print(content)
    print()
    print("-" * 46)
    print("Resume from the 'Resume Prompt' section above.")
    print("When the work is done, delete .session-handoff/HANDOFF.md "
          "(or it will load again next session).")
    print("-" * 46)

    lib.log(project_dir, "Loaded handoff into new session"
                          + (" (flagged stale)" if stale_note else "") + ".")


if __name__ == "__main__":
    main()
