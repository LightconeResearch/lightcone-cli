#!/bin/bash
# SessionStart hook: prepend the project venv to PATH for every Bash command
# Claude Code spawns this session.
#
# We don't `source` activate -- we only need PATH and VIRTUAL_ENV. Sourcing
# activate also runs prompt mutations and defines a deactivate function we
# don't want, and any non-zero status under `set -e` would silently skip
# the writeback (the failure mode behind issue #103). Writing the two
# exports directly to CLAUDE_ENV_FILE is the documented mechanism and is
# what every downstream hook implicitly relies on for `astra` / `lc` to
# resolve to the project venv rather than whatever system install happens
# to be on PATH.

VENV="$CLAUDE_PROJECT_DIR/.venv"
[ -d "$VENV/bin" ] || exit 0
[ -n "$CLAUDE_ENV_FILE" ] || exit 0

{
    echo "export VIRTUAL_ENV=$VENV"
    echo "export PATH=$VENV/bin:\$PATH"
} >> "$CLAUDE_ENV_FILE"

exit 0
