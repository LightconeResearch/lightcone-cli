#!/bin/bash
# Setup script for prism-build skill
# Two modes: --validate (pre-planning) and --activate (post-approval)

set -euo pipefail

MODE=""
UNIVERSE="baseline"
MAX_ITERATIONS=25

while [[ $# -gt 0 ]]; do
    case "$1" in
        --validate) MODE="validate"; shift ;;
        --activate) MODE="activate"; shift ;;
        --universe)
            UNIVERSE="$2"
            shift 2
            ;;
        --max-iterations)
            MAX_ITERATIONS="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: setup-prism-build.sh --validate|--activate --universe NAME --max-iterations N" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$MODE" ]]; then
    echo "Error: must specify --validate or --activate" >&2
    exit 1
fi

# ─── Validate mode ───────────────────────────────────────────────────

if [[ "$MODE" == "validate" ]]; then

    # Check asp.yaml exists
    if [[ ! -f "asp.yaml" ]]; then
        echo "Error: asp.yaml not found in $(pwd)"
        echo "Run /prism-new to create an analysis specification first."
        exit 1
    fi

    # Check asp CLI available
    if ! command -v asp &>/dev/null; then
        echo "Error: asp CLI not found. Run: pip install asp"
        exit 1
    fi

    # Validate spec
    echo "Validating asp.yaml..."
    validation_output=$(asp validate asp.yaml 2>&1) || {
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
        asp universe generate -n "$UNIVERSE" 2>&1
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
    if [[ ! -f ".claude/build-plan-${UNIVERSE}.md" ]]; then
        echo "Warning: No build plan found at .claude/build-plan-${UNIVERSE}.md"
        echo "The loop will proceed without a plan."
    fi

    # Warn if loop already active
    if [[ -f ".claude/ralph-loop.local.md" ]]; then
        existing_iter=$(grep '^iteration:' .claude/ralph-loop.local.md 2>/dev/null | awk '{print $2}' || echo "?")
        echo "Warning: Active loop detected (iteration ${existing_iter}). Overwriting."
    fi

    # Ensure ralph-loop plugin is available
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
            git clone https://github.com/anthropics/claude-plugins-official.git "$MARKETPLACE" 2>&1 || true
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
