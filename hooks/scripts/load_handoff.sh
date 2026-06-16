#!/bin/bash
# Generic session loader — works for any team or project type.
# Injects HANDOFF.md content at the start of every new session, if one exists.

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
HANDOFF_FILE="$PROJECT_DIR/HANDOFF.md"

if [ -f "$HANDOFF_FILE" ]; then
    echo "=============================================="
    echo "        PREVIOUS SESSION HANDOFF FOUND        "
    echo "=============================================="
    echo ""
    cat "$HANDOFF_FILE"
    echo ""
    echo "----------------------------------------------"
    echo "Resume from the 'Resume Prompt' section above."
    echo "When the work is done, you can delete HANDOFF.md."
    echo "----------------------------------------------"
fi
