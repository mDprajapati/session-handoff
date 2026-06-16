"""
Tests for the session-handoff plugin.

Run with:  pytest

These cover the team-critical behaviours: secret redaction, config layering,
git-ignore safety, timestamped history, staleness parsing, tool-use capture, and
the local fallback resume line. No network calls are made — the API path is
exercised only via local-only / no-key fallbacks.
"""

import importlib
import os
import sys

import pytest

# Make the hook scripts importable.
SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "hooks", "scripts")
sys.path.insert(0, SCRIPTS)

lib = importlib.import_module("handoff_lib")
auto = importlib.import_module("auto_handoff")
loader = importlib.import_module("load_handoff")


# ─── Redaction ──────────────────────────────────────────────────────────────────

# Fake secrets are assembled from fragments so the source file contains no
# literal token — otherwise GitHub push protection (correctly) blocks the push.
# The runtime values still exercise the redaction patterns end to end.
@pytest.mark.parametrize("secret", [
    "sk-" + "ant-" + "abcd1234efgh5678ijkl",
    "AK" + "IA" + "IOSFODNN7EXAMPLE",
    "gh" + "p_" + "0123456789abcdef0123456789abcdef0123",
    "xo" + "xb-" + "1234567890-abcdefghijklmno",
])
def test_redact_masks_known_secrets(secret):
    out = lib.redact(f"here is a token {secret} in context")
    assert secret not in out
    assert "REDACTED" in out


def test_redact_key_value_assignment():
    out = lib.redact("password = hunter2supersecret")
    assert "hunter2supersecret" not in out
    assert "REDACTED" in out


def test_redact_private_key_block():
    pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "MIIBOgIBAAJBAKj34GkxFhD\n"
           "-----END RSA PRIVATE KEY-----")
    out = lib.redact(f"key:\n{pem}\nend")
    assert "MIIBOgIBAAJBAKj34GkxFhD" not in out


def test_redact_keeps_normal_prose():
    text = "We finished the auth refactor and ran the migration."
    assert lib.redact(text) == text


def test_redact_handles_empty():
    assert lib.redact("") == ""
    assert lib.redact(None) is None


# ─── Config ──────────────────────────────────────────────────────────────────────

def test_config_defaults(tmp_path):
    cfg = lib.load_config(str(tmp_path))
    assert cfg["model"] == lib.DEFAULTS["model"]
    assert cfg["local_only"] is False


def test_config_file_override(tmp_path):
    (tmp_path / lib.CONFIG_FILENAME).write_text(
        '{"local_only": true, "stale_hours": 48}', encoding="utf-8")
    cfg = lib.load_config(str(tmp_path))
    assert cfg["local_only"] is True
    assert cfg["stale_hours"] == 48


def test_env_overrides_file(tmp_path, monkeypatch):
    (tmp_path / lib.CONFIG_FILENAME).write_text('{"max_turns": 10}', encoding="utf-8")
    monkeypatch.setenv("SESSION_HANDOFF_MAX_TURNS", "99")
    monkeypatch.setenv("SESSION_HANDOFF_LOCAL_ONLY", "1")
    cfg = lib.load_config(str(tmp_path))
    assert cfg["max_turns"] == 99
    assert cfg["local_only"] is True


def test_config_ignores_malformed_file(tmp_path):
    (tmp_path / lib.CONFIG_FILENAME).write_text("{not valid json", encoding="utf-8")
    cfg = lib.load_config(str(tmp_path))  # must not raise
    assert cfg["model"] == lib.DEFAULTS["model"]


def test_config_ignores_bad_env_value(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_HANDOFF_MAX_TURNS", "not-a-number")
    cfg = lib.load_config(str(tmp_path))
    assert cfg["max_turns"] == lib.DEFAULTS["max_turns"]


# ─── Timestamp / staleness ────────────────────────────────────────────────────────

def test_generated_at_roundtrip():
    marker = lib.generated_at_marker()
    parsed = lib.parse_generated_at(f"# Handoff\n{marker}\nbody")
    assert parsed is not None


def test_parse_generated_at_missing():
    assert lib.parse_generated_at("no marker here") is None


# ─── Transcript parsing ───────────────────────────────────────────────────────────

def test_extract_turns_text_and_tools():
    messages = [
        {"message": {"role": "user", "content": "fix the auth bug"}},
        {"message": {"role": "assistant", "content": [
            {"type": "text", "text": "Editing the file."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "auth.py"}},
        ]}},
        {"message": {"role": "user", "content": [
            {"type": "tool_result", "is_error": True},
        ]}},
    ]
    turns = auto.extract_turns(messages)
    assert turns[0]["content"] == "fix the auth bug"
    assert any("Edit(file_path=auth.py)" in t for t in turns[1]["tools"])
    assert any("ERROR" in t for t in turns[2]["tools"])


def test_read_transcript_strips_bom(tmp_path):
    # A BOM-prefixed JSONL must not cause the first message to be dropped.
    path = tmp_path / "t.jsonl"
    path.write_text('{"message":{"role":"user","content":"first"}}\n'
                    '{"message":{"role":"user","content":"second"}}\n',
                    encoding="utf-8-sig")
    turns = auto.extract_turns(auto.read_transcript(str(path)))
    assert [t["content"] for t in turns] == ["first", "second"]


def test_build_activity_trace_orders_recent_last():
    turns = [
        {"role": "assistant", "content": "", "tools": ["A", "B"]},
        {"role": "assistant", "content": "", "tools": ["C"]},
    ]
    assert auto.build_activity_trace(turns) == ["A", "B", "C"]


# ─── Fallback summary ─────────────────────────────────────────────────────────────

def test_fallback_uses_last_concrete_request():
    turns = [
        {"role": "user", "content": "Start the SEO audit for acme.com", "tools": []},
        {"role": "assistant", "content": "Working on it.", "tools": []},
        {"role": "user", "content": "ok", "tools": []},  # trivial — should be skipped
    ]
    out = auto.build_fallback_summary(turns)
    assert "acme.com" in out
    # The forbidden vague filler must not appear as the resume line.
    assert "continue from where the last session stopped" not in out.lower()


# ─── Writer: git-ignore + history ─────────────────────────────────────────────────

def test_ensure_gitignored_appends_entry(tmp_path):
    os.mkdir(tmp_path / ".git")  # pretend it's a git repo
    auto.ensure_gitignored(str(tmp_path))
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".session-handoff/" in gi


def test_ensure_gitignored_no_duplicate(tmp_path):
    os.mkdir(tmp_path / ".git")
    (tmp_path / ".gitignore").write_text(".session-handoff/\n", encoding="utf-8")
    auto.ensure_gitignored(str(tmp_path))
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gi.count(".session-handoff") == 1


def test_ensure_gitignored_skips_non_repo(tmp_path):
    auto.ensure_gitignored(str(tmp_path))  # no .git dir
    assert not (tmp_path / ".gitignore").exists()


def test_write_handoff_creates_latest_and_history(tmp_path):
    cfg = lib.load_config(str(tmp_path))
    path = auto.write_handoff(str(tmp_path), "## Work Type\nDev\n", "auto", cfg)
    assert os.path.exists(path)
    assert path == lib.latest_path(str(tmp_path))
    history = os.listdir(lib.history_dir(str(tmp_path)))
    assert len(history) == 1 and history[0].startswith("HANDOFF-")


def test_write_handoff_redacts(tmp_path):
    cfg = lib.load_config(str(tmp_path))
    secret = "sk-" + "ant-" + "abcd1234efgh5678ijkl"  # assembled; see note above
    body = f"## Important Context\nkey: {secret}\n"
    path = auto.write_handoff(str(tmp_path), body, "auto", cfg)
    content = open(path, encoding="utf-8").read()
    assert secret not in content


def test_write_handoff_prunes_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_HANDOFF_HISTORY_KEEP", "2")
    cfg = lib.load_config(str(tmp_path))
    hdir = lib.history_dir(str(tmp_path))
    os.makedirs(hdir, exist_ok=True)
    # Seed older history files.
    for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
        open(os.path.join(hdir, f"HANDOFF-{stamp}.md"), "w").close()
    auto.write_handoff(str(tmp_path), "body", "auto", cfg)
    remaining = sorted(os.listdir(hdir))
    assert len(remaining) == 2  # pruned to keep=2 (newest survive)


# ─── Logging ──────────────────────────────────────────────────────────────────────

def test_log_writes_line(tmp_path):
    lib.log(str(tmp_path), "hello world")
    content = open(lib.log_path(str(tmp_path)), encoding="utf-8").read()
    assert "hello world" in content
