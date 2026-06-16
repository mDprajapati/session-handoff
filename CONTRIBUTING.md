# Contributing to session-handoff

Thanks for helping improve the plugin. It is small on purpose — standard-library
Python only, no runtime dependencies — so it stays easy to audit and runs anywhere
Claude Code does.

## Project layout

```
.claude-plugin/plugin.json     # plugin manifest (name, version, keywords)
hooks/hooks.json               # wires PreCompact + SessionStart to the scripts
hooks/scripts/handoff_lib.py   # shared config, section schema, redaction, logging
hooks/scripts/auto_handoff.py  # PreCompact: writes the handoff
hooks/scripts/load_handoff.py  # SessionStart: loads the handoff
skills/session-handoff/SKILL.md # on-demand "create a handoff" guidance
tests/                         # pytest suite
```

## Development setup

Requires Python 3.7+ (no third-party runtime dependencies).

```bash
pip install pytest
pytest                         # run the suite
python -m compileall hooks/scripts   # syntax check
```

CI runs the tests, byte-compiles the hooks, and validates the JSON manifests on
Linux and Windows for Python 3.8 and 3.12. Please make sure `pytest` passes
locally before opening a PR.

## Guidelines

- **No runtime dependencies.** Standard library only, so the hooks run in any
  environment without `pip install`. Test-only dependencies (e.g. `pytest`) are
  fine.
- **Keep the section schema in one place.** The handoff sections are defined in
  `handoff_lib.SECTIONS`. If you change them, the summarizer prompt and SKILL.md
  must reflect the same set — update the doc, don't duplicate the list.
- **Never break the session.** Hooks must fail safe: log the problem and exit
  cleanly rather than raising. The handoff is a convenience, not a gatekeeper.
- **Privacy by default.** Anything that could leave the machine (the API path) or
  land on disk must go through `handoff_lib.redact` first. If you add a new sink,
  redact before it.
- **Add a test.** New behaviour needs a test in `tests/`. Bug fixes should add a
  regression test that fails before the fix.
- **Bump the version.** Update `version` in both `plugin.json` and `SKILL.md`, and
  add a `CHANGELOG.md` entry. We follow [Semantic Versioning](https://semver.org/).

## Reporting bugs

Open an issue using the bug template and, when possible, attach the relevant lines
from `.session-handoff/handoff.log` (with secrets removed).
