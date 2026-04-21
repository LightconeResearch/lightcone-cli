#!/bin/bash
# Activate Python virtual environment if it exists in the project directory
# This hook runs at SessionStart to ensure the venv is active for Claude Code

set -e

# Check if we have a project directory
if [ -z "$CLAUDE_PROJECT_DIR" ]; then
    exit 0
fi

# Check for .venv in project directory
VENV_DIR="$CLAUDE_PROJECT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    exit 0
fi

# Determine activate script path based on OS
if [ -f "$VENV_DIR/bin/activate" ]; then
    ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    ACTIVATE_SCRIPT="$VENV_DIR/Scripts/activate"
else
    exit 0
fi

# Capture environment before activation
BEFORE_PATH="$PATH"
BEFORE_VIRTUAL_ENV="${VIRTUAL_ENV:-}"

# Source the activation script
# shellcheck source=/dev/null
source "$ACTIVATE_SCRIPT"

# If CLAUDE_ENV_FILE is set, write environment changes to it
if [ -n "$CLAUDE_ENV_FILE" ]; then
    # Write PATH update
    echo "PATH=$PATH" >> "$CLAUDE_ENV_FILE"

    # Write VIRTUAL_ENV
    echo "VIRTUAL_ENV=$VIRTUAL_ENV" >> "$CLAUDE_ENV_FILE"
fi

exit 0
