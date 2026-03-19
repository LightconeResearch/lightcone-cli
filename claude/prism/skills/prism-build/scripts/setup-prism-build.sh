#!/bin/bash
# Setup script for prism-build skill
# Two modes: --validate (pre-planning) and --activate (post-approval)

set -euo pipefail

MODE=""
UNIVERSE="baseline"
MAX_ITERATIONS=25
MAX_ITERATIONS_EXPLICIT=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --validate) MODE="validate"; shift ;;
        --activate) MODE="activate"; shift ;;
        --resume) MODE="resume"; shift ;;
        --universe)
            UNIVERSE="$2"
            shift 2
            ;;
        --max-iterations)
            MAX_ITERATIONS="$2"
            MAX_ITERATIONS_EXPLICIT=true
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: setup-prism-build.sh --validate|--activate|--resume --universe NAME --max-iterations N" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$MODE" ]]; then
    echo "Error: must specify --validate or --activate" >&2
    exit 1
fi

# Validate universe name — must be safe for sed substitution and file paths
if [[ ! "$UNIVERSE" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Error: Universe name must contain only letters, numbers, underscores, and hyphens." >&2
    echo "Got: '${UNIVERSE}'" >&2
    exit 1
fi

# ─── Validate mode ───────────────────────────────────────────────────

if [[ "$MODE" == "validate" ]]; then

    # Check astra.yaml exists
    if [[ ! -f "astra.yaml" ]]; then
        echo "Error: astra.yaml not found in $(pwd)"
        echo "Run /prism-new to create an analysis specification first."
        exit 1
    fi

    # Check astra CLI available
    if ! command -v astra &>/dev/null; then
        echo "Error: astra CLI not found. Run: pip install astra"
        exit 1
    fi

    # Validate spec
    echo "Validating astra.yaml..."
    validation_output=$(astra validate astra.yaml 2>&1) || {
        echo "Validation failed:"
        echo "$validation_output"
        echo ""
        echo "Fix validation errors before building."
        exit 1
    }
    echo "Validation: passed"

    # Check/create universe
    if [[ ! -f "universes/${UNIVERSE}.yaml" ]]; then
        echo "Universe '${UNIVERSE}' does not exist. Creating..."
        astra universe generate -n "$UNIVERSE" 2>&1
        echo "Universe created: universes/${UNIVERSE}.yaml"
    else
        echo "Universe: ${UNIVERSE} (exists)"
    fi

    # Check prism CLI
    if ! command -v prism &>/dev/null; then
        echo "Warning: prism CLI not found. Materialization commands will fail."
        echo "Run: pip install prism"
    fi

    # Summary
    echo ""
    echo "Ready to plan build for universe: ${UNIVERSE}"
    echo "Max iterations: ${MAX_ITERATIONS}"

    exit 0
fi

# ─── Activate mode ───────────────────────────────────────────────────

if [[ "$MODE" == "activate" ]]; then

    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    PROMPT_TEMPLATE="${SCRIPT_DIR}/../assets/loop-prompt.md"

    # Check template exists
    if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
        echo "Error: loop-prompt.md template not found at ${PROMPT_TEMPLATE}" >&2
        exit 1
    fi

    # Check build plan exists
    if [[ ! -f ".prism/plans/build-plan-${UNIVERSE}.md" ]]; then
        echo "Warning: No build plan found at .prism/plans/build-plan-${UNIVERSE}.md"
        echo "The loop will proceed without a plan."
    fi

    # Fail hard if loop already active — don't silently overwrite a live loop
    if [[ -f ".claude/ralph-loop.local.md" ]]; then
        existing_iter=$(grep '^iteration:' .claude/ralph-loop.local.md 2>/dev/null | awk '{print $2}' || echo "?")
        echo "Error: An active loop state file already exists (iteration ${existing_iter})." >&2
        echo "  To resume the interrupted loop: setup-prism-build.sh --resume" >&2
        echo "  To start fresh: delete .claude/ralph-loop.local.md first, then re-run --activate" >&2
        exit 1
    fi

    # Ensure ralph-loop plugin is available
    # Update MARKETPLACE_URL if the official plugin repository moves.
    MARKETPLACE_URL="https://github.com/anthropics/claude-plugins-official.git"
    RALPH_PLUGIN="$HOME/.claude/plugins/marketplaces/claude-plugins-official/plugins/ralph-loop"
    MARKETPLACE="$HOME/.claude/plugins/marketplaces/claude-plugins-official"

    if [[ ! -d "$RALPH_PLUGIN" ]]; then
        echo "ralph-loop plugin not found. Attempting to install..."

        if [[ -d "$MARKETPLACE/.git" ]]; then
            # Marketplace exists but plugin missing — pull latest
            echo "Updating plugin marketplace..."
            git -C "$MARKETPLACE" pull --ff-only 2>&1 || true
        elif [[ ! -d "$MARKETPLACE" ]]; then
            # No marketplace at all — clone it
            echo "Cloning plugin marketplace..."
            mkdir -p "$HOME/.claude/plugins/marketplaces"
            git clone "$MARKETPLACE_URL" "$MARKETPLACE" 2>&1 || true
        fi

        # Check again after update/clone
        if [[ ! -d "$RALPH_PLUGIN" ]]; then
            echo ""
            echo "Error: ralph-loop plugin could not be installed." >&2
            echo "The stop hook is required for /prism-build to loop." >&2
            echo "" >&2
            echo "Manual install: /plugin install ralph-loop@claude-plugins-official" >&2
            # Clean up — don't leave a state file that traps the user
            rm -f .claude/ralph-loop.local.md
            exit 1
        fi

        echo "ralph-loop plugin found after update."
    fi

    # Verify the stop hook exists within the plugin
    if [[ ! -f "$RALPH_PLUGIN/hooks/stop-hook.sh" ]]; then
        echo "Error: ralph-loop plugin is present but hooks/stop-hook.sh is missing." >&2
        echo "The plugin may be corrupted. Try: /plugin install ralph-loop@claude-plugins-official" >&2
        exit 1
    fi

    # Template the prompt
    prompt_body=$(sed "s/{{UNIVERSE}}/${UNIVERSE}/g" "$PROMPT_TEMPLATE")

    # Create state file
    mkdir -p .claude
    cat > .claude/ralph-loop.local.md <<EOF
---
active: true
iteration: 1
max_iterations: ${MAX_ITERATIONS}
completion_promise: "BUILD_COMPLETE"
session_id: ${CLAUDE_CODE_SESSION_ID:-}
universe: ${UNIVERSE}
started_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
---

${prompt_body}
EOF

    echo "Loop activated for universe: ${UNIVERSE}"
    echo "  State file: .claude/ralph-loop.local.md"
    echo "  Max iterations: ${MAX_ITERATIONS}"
    echo "  Completion promise: BUILD_COMPLETE"
    echo ""
    echo "The stop hook will re-inject the build prompt on each exit."
    echo "To cancel: /cancel-ralph"

    exit 0
fi

# ─── Resume mode ────────────────────────────────────────────────────

if [[ "$MODE" == "resume" ]]; then

    RALPH_STATE_FILE=".claude/ralph-loop.local.md"

    if [[ ! -f "$RALPH_STATE_FILE" ]]; then
        echo "No active loop to resume (state file not found)."
        echo "Run /prism-build to start a new build."
        exit 1
    fi

    # Read current state from file
    CURRENT_ITER=$(grep '^iteration:' "$RALPH_STATE_FILE" | awk '{print $2}')
    OLD_SESSION=$(grep '^session_id:' "$RALPH_STATE_FILE" | awk '{print $2}')
    CURRENT_MAX=$(grep '^max_iterations:' "$RALPH_STATE_FILE" | awk '{print $2}')

    # Read universe from dedicated frontmatter field
    LOOP_UNIVERSE=$(grep '^universe:' "$RALPH_STATE_FILE" | awk '{print $2}')

    # Update session_id to claim this loop for the current session
    TEMP_FILE="${RALPH_STATE_FILE}.tmp.$$"
    sed "s/^session_id: .*/session_id: ${CLAUDE_CODE_SESSION_ID:-}/" "$RALPH_STATE_FILE" > "$TEMP_FILE"

    # If --max-iterations was explicitly passed, update it in the state file
    if [[ "$MAX_ITERATIONS_EXPLICIT" == "true" ]]; then
        sed -i "s/^max_iterations: .*/max_iterations: ${MAX_ITERATIONS}/" "$TEMP_FILE"
        CURRENT_MAX="${MAX_ITERATIONS}"
    fi

    mv "$TEMP_FILE" "$RALPH_STATE_FILE"

    # Compute remaining iterations
    REMAINING=$(( ${CURRENT_MAX:-0} - ${CURRENT_ITER:-1} + 1 ))

    echo "Resumed loop for universe: ${LOOP_UNIVERSE:-unknown}"
    echo "  Continuing from iteration: ${CURRENT_ITER:-?} / ${CURRENT_MAX:-?}"
    echo "  Remaining iterations: ${REMAINING}"
    echo "  Session updated: ${OLD_SESSION:-<unset>} -> ${CLAUDE_CODE_SESSION_ID:-<unset>}"
    if [[ "$MAX_ITERATIONS_EXPLICIT" == "true" ]]; then
        echo "  Max iterations updated to: ${MAX_ITERATIONS}"
    fi

    exit 0
fi
