#!/usr/bin/env python3
"""
Shared utilities for the session-handoff plugin.

This module is the single source of truth for:
  - configuration (env vars + optional .session-handoff.json)
  - the on-disk layout (where handoffs and logs live)
  - the handoff section schema (used by both the writer prompt and the skill)
  - secret redaction (so nothing sensitive is sent to the API or written to disk)
  - lightweight logging (so failures are debuggable instead of silent)

Both auto_handoff.py (writer, PreCompact) and load_handoff.py (loader,
SessionStart) import from here so their behaviour can never drift apart.

No third-party dependencies — standard library only, so it runs anywhere
Python 3.7+ is available (Windows, macOS, Linux).
"""

import json
import os
import re
from datetime import datetime, timezone

# ─── Layout ────────────────────────────────────────────────────────────────────
#
# Everything the plugin writes lives under a single git-ignored directory so it
# never pollutes the user's repository or causes merge conflicts on shared
# branches.
#
#   <project>/.session-handoff/
#       HANDOFF.md                      <- the "latest" pointer (what the loader reads)
#       history/HANDOFF-<stamp>.md      <- immutable, timestamped history
#       handoff.log                     <- rotating debug log

HANDOFF_DIR_NAME = ".session-handoff"
LATEST_FILENAME = "HANDOFF.md"
HISTORY_DIRNAME = "history"
LOG_FILENAME = "handoff.log"
CONFIG_FILENAME = ".session-handoff.json"

# Max log size before it is truncated (keeps the file from growing unbounded).
LOG_MAX_BYTES = 256 * 1024


def handoff_dir(project_dir):
    """Absolute path to the plugin's working directory inside the project."""
    return os.path.join(project_dir, HANDOFF_DIR_NAME)


def latest_path(project_dir):
    """Path to the 'latest' handoff that the loader reads on SessionStart."""
    return os.path.join(handoff_dir(project_dir), LATEST_FILENAME)


def history_dir(project_dir):
    return os.path.join(handoff_dir(project_dir), HISTORY_DIRNAME)


def log_path(project_dir):
    return os.path.join(handoff_dir(project_dir), LOG_FILENAME)


# ─── Configuration ───────────────────────────────────────────────────────────────
#
# Defaults are sane for the common case. Anything can be overridden via
# environment variables (handy for CI / shared machines) or a project-local
# .session-handoff.json file (handy for per-repo policy). Env vars win over the
# file so an individual can override a committed project default.

DEFAULTS = {
    # Anthropic model used for the AI summary. Overridable so a deprecated model
    # id can be swapped without a code change.
    "model": "claude-haiku-4-5-20251001",
    # How many recent turns to feed the summarizer.
    "max_turns": 40,
    # Per-turn character cap when building the conversation snippet.
    "max_snippet_chars": 1000,
    # Output token budget for the summary.
    "max_tokens": 1200,
    # API timeout (seconds). Kept short so a slow API never stalls the session
    # up to the hook timeout — we fall through to the local summary instead.
    "api_timeout": 20,
    # Number of API attempts before falling back to the local summary.
    "api_attempts": 2,
    # When true, never call the API — build the handoff locally only. Use this
    # for sensitive repos where the transcript must not leave the machine.
    "local_only": False,
    # Redact obvious secrets before sending to the API or writing to disk.
    "redact_secrets": True,
    # A handoff older than this many hours is shown as "possibly stale".
    "stale_hours": 24,
    # How many timestamped history files to keep (older ones are pruned).
    "history_keep": 20,
}

# Env var name -> (config key, caster)
_ENV_MAP = {
    "SESSION_HANDOFF_MODEL": ("model", str),
    "SESSION_HANDOFF_MAX_TURNS": ("max_turns", int),
    "SESSION_HANDOFF_MAX_SNIPPET_CHARS": ("max_snippet_chars", int),
    "SESSION_HANDOFF_MAX_TOKENS": ("max_tokens", int),
    "SESSION_HANDOFF_API_TIMEOUT": ("api_timeout", int),
    "SESSION_HANDOFF_API_ATTEMPTS": ("api_attempts", int),
    "SESSION_HANDOFF_LOCAL_ONLY": ("local_only", "bool"),
    "SESSION_HANDOFF_REDACT": ("redact_secrets", "bool"),
    "SESSION_HANDOFF_STALE_HOURS": ("stale_hours", int),
    "SESSION_HANDOFF_HISTORY_KEEP": ("history_keep", int),
}


def _as_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_config(project_dir):
    """
    Resolve config from defaults < project file < environment.

    Never raises: a malformed config file or bad env value falls back to the
    default for that key so a typo can't disable the plugin entirely.
    """
    config = dict(DEFAULTS)

    # Layer 1: optional project-local file.
    cfg_file = os.path.join(project_dir, CONFIG_FILENAME)
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            if isinstance(file_cfg, dict):
                for key, value in file_cfg.items():
                    if key in DEFAULTS:
                        config[key] = value
        except Exception:
            # Malformed file: ignore and keep defaults (logged by the caller).
            pass

    # Layer 2: environment variables (highest priority).
    for env_name, (key, caster) in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _as_bool(raw) if caster == "bool" else caster(raw)
        except (ValueError, TypeError):
            # Bad override: keep whatever we had.
            pass

    return config


# ─── Section schema (single source of truth) ─────────────────────────────────────
#
# Both the summarizer prompt and SKILL.md describe the same sections. Defining
# them once here prevents the two from drifting out of sync.

SECTIONS = [
    ("Work Type",
     "One line: what kind of work is this? "
     "e.g. \"Development - Fixing auth token refresh\" or "
     "\"SEO - On-page audit for client website\"."),
    ("What Was Completed",
     "Bullet list of things finished or decided in this session."),
    ("Current Status",
     "What is in-progress RIGHT NOW? What was the last action taken?"),
    ("Pending Items",
     "Exact tasks still to do, in priority order."),
    ("Important Context",
     "Critical facts the next session MUST know: names, companies, clients, "
     "contacts; numbers, dates, deadlines, budgets, targets; decisions and why; "
     "constraints or preferences discovered; links, files, or document names."),
    ("Activity Trace",
     "For technical work, a compact list of concrete actions: files edited, "
     "commands run, tests run and their outcome. Omit if not applicable."),
    ("Watch Out For",
     "Any blockers, risks, open questions, or things that might go wrong."),
    ("Resume Prompt",
     "ONE specific sentence the user can paste into a new session to resume. "
     "Never vague: state WHAT work, WHERE it was left, and WHAT comes next."),
]


def sections_for_prompt():
    """Render the section schema as instructions for the summarizer prompt."""
    out = []
    for title, guidance in SECTIONS:
        out.append(f"## {title}\n({guidance})")
    return "\n\n".join(out)


# ─── Secret redaction ────────────────────────────────────────────────────────────
#
# Conservative, pattern-based redaction. The goal is to strip the obvious,
# high-risk secrets (keys, tokens, passwords) before any text leaves the machine
# or is written to disk. It is intentionally narrow to avoid mangling legitimate
# prose — it is a safety net, not a guarantee.

_REDACTION_PATTERNS = [
    # Anthropic / OpenAI style keys.
    (re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{8,})"), "sk-ant-***REDACTED***"),
    (re.compile(r"\b(sk-[A-Za-z0-9]{20,})"), "sk-***REDACTED***"),
    # AWS access key id.
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "AKIA***REDACTED***"),
    # GitHub tokens.
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"), "ghX_***REDACTED***"),
    # Slack tokens.
    (re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})"), "xox-***REDACTED***"),
    # Google API keys.
    (re.compile(r"\b(AIza[0-9A-Za-z_\-]{30,})"), "AIza***REDACTED***"),
    # Bearer tokens in Authorization headers.
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"), r"\1***REDACTED***"),
    # Generic "key/secret/token/password = value" assignments.
    (re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)"
        r"(\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})"),
     r"\1\2\3***REDACTED***"),
    # PEM private key blocks.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "***REDACTED PRIVATE KEY***"),
]


def redact(text):
    """Return text with obvious secrets masked. Never raises."""
    if not text:
        return text
    try:
        for pattern, replacement in _REDACTION_PATTERNS:
            text = pattern.sub(replacement, text)
    except Exception:
        # If redaction itself fails, returning the original is worse than safe:
        # we'd rather drop the content than leak it.
        return "***REDACTION ERROR — content withheld***"
    return text


# ─── Logging ────────────────────────────────────────────────────────────────────


def log(project_dir, message):
    """
    Append a timestamped line to .session-handoff/handoff.log.

    Best-effort and never raises — logging must never break the hook. The log is
    truncated when it grows past LOG_MAX_BYTES so it can't fill the disk.
    """
    try:
        os.makedirs(handoff_dir(project_dir), exist_ok=True)
        path = log_path(project_dir)

        # Cheap rotation: if oversized, keep the tail.
        try:
            if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-(LOG_MAX_BYTES // 2):]
                with open(path, "w", encoding="utf-8") as f:
                    f.write(tail)
        except Exception:
            pass

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        # Last resort: swallow. A broken log must not break the handoff.
        pass


# ─── Timestamp helpers ────────────────────────────────────────────────────────────
#
# We embed a machine-readable ISO-8601 timestamp inside each handoff so the
# loader can judge staleness reliably, independent of file mtimes (which git
# checkouts and copies can reset).

_META_MARKER = "<!-- session-handoff:generated_at="


def generated_at_marker():
    """A hidden, machine-readable timestamp line to embed in each handoff."""
    iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return f"{_META_MARKER}{iso} -->"


def parse_generated_at(content):
    """
    Extract the embedded generation time from a handoff's content.

    Returns a timezone-aware datetime, or None if the marker is absent/unparseable.
    """
    try:
        start = content.index(_META_MARKER) + len(_META_MARKER)
        end = content.index(" -->", start)
        return datetime.fromisoformat(content[start:end].strip())
    except (ValueError, AttributeError):
        return None
