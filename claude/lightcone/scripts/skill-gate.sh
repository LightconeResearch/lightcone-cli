#!/bin/bash
# PreToolUse(Bash|Write|Edit|Skill) hook: gate the first action in a
# Lightcone/ASTRA project until the /lightcone skill is activated.
#
# Thin wrapper over `lc hook pretool` (the gate logic lives there, in Python,
# tested). `lc` resolves from the project venv via PATH, prepended at
# SessionStart by activate-venv.sh -- same mechanism validate-on-save.sh
# relies on. If `lc` can't be found, pass through (fail open): losing the
# gate is far better than blocking every tool call.
command -v lc >/dev/null 2>&1 || exit 0
exec lc hook pretool
