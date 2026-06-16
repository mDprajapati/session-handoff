# Changelog

All notable changes to the session-handoff plugin are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-06-16

Hardening pass to make the plugin safe for whole-team use.

### Added
- **Git-safe output.** Handoffs are written under a `.session-handoff/` directory
  that is automatically added to the project's `.gitignore`, so they never get
  committed or cause merge conflicts on shared branches.
- **Timestamped history.** Every handoff is also kept under
  `.session-handoff/history/` (pruned to the most recent N), giving history for
  free instead of last-write-wins clobbering.
- **Staleness detection.** Each handoff embeds a machine-readable timestamp; on
  load, anything older than the threshold (default 24h) is flagged *possibly
  stale* instead of being presented as the live task.
- **Privacy controls.** Secrets (API keys, tokens, passwords, private keys, bearer
  tokens) are redacted before the transcript is sent to the API or written to
  disk. A new `SESSION_HANDOFF_LOCAL_ONLY=1` mode skips the API entirely.
- **Logging.** Every run is recorded in `.session-handoff/handoff.log` (rotating),
  so failures are debuggable instead of silent.
- **Tool-use capture.** The handoff now includes an *Activity Trace* of file edits,
  commands run, and tool errors — the real state of technical work.
- **Configuration layer.** Model id, turn count, token budget, API timeout/retries,
  staleness threshold, and history retention are configurable via environment
  variables or a project-local `.session-handoff.json`.
- **Shared library (`handoff_lib.py`).** Single source of truth for config, the
  section schema, redaction, and logging — used by both hooks and referenced by
  the skill so they cannot drift.
- **Tests & CI.** A `pytest` suite and a GitHub Actions workflow that runs tests,
  byte-compiles the hooks, and validates the JSON manifests.
- **Marketplace distribution.** A `.claude-plugin/marketplace.json` makes the repo
  installable with `/plugin marketplace add mDprajapati/session-handoff` +
  `/plugin install session-handoff@session-handoff`, and a checked-in
  `.claude/settings.json` enables zero-command team auto-install. Replaces the
  previous (unsupported) "clone into ~/.claude/plugins/" instructions.

### Changed
- **Cross-platform loader.** `load_handoff.sh` (bash-only) is replaced by
  `load_handoff.py`, so it runs identically on Windows, macOS, and Linux.
- **Robust API call.** Shorter timeout (default 20s) with retries, falling through
  to a local summary immediately rather than stalling the session.
- **Better local fallback.** The fallback now extracts the last concrete user
  request as the resume line, instead of emitting the vague "continue from where
  the last session stopped" filler the skill forbids.

### Removed
- `hooks/scripts/load_handoff.sh` (replaced by the cross-platform Python loader).

## [0.1.0]

- Initial release: PreCompact hook generates `HANDOFF.md`, SessionStart hook loads
  it, plus an on-demand skill.
