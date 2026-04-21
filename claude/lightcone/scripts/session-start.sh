#!/bin/bash
# SessionStart hook: Show ASTRA analysis summary when entering a project
# Provides context about the current analysis state

# Read JSON input from stdin
input=$(cat)

# Get the current working directory
cwd=$(echo "$input" | jq -r '.cwd // empty')

if [ -z "$cwd" ]; then
    exit 0
fi

cd "$cwd" 2>/dev/null || exit 0

# Check for active lc-build loop (crash recovery)
if [ -f ".claude/ralph-loop.local.md" ]; then
    loop_iter=$(grep '^iteration:' .claude/ralph-loop.local.md 2>/dev/null | awk '{print $2}')
    loop_max=$(grep '^max_iterations:' .claude/ralph-loop.local.md 2>/dev/null | awk '{print $2}')
    loop_session=$(grep '^session_id:' .claude/ralph-loop.local.md 2>/dev/null | awk '{print $2}')
    # Read universe from dedicated frontmatter field (falls back gracefully for old state files)
    loop_universe=$(grep '^universe:' .claude/ralph-loop.local.md 2>/dev/null | awk '{print $2}')
    loop_universe="${loop_universe:-unknown}"

    current_session="${CLAUDE_CODE_SESSION_ID:-}"

    # If the loop belongs to a different active session, show informational message only.
    # This prevents a concurrent session from accidentally resuming or cancelling another session's loop.
    if [ -n "$loop_session" ] && [ -n "$current_session" ] && [ "$loop_session" != "$current_session" ]; then
        loop_info="lc-build loop active in another session (universe: ${loop_universe}, iteration ${loop_iter:-?}/${loop_max:-?}). This session is unaffected — manage the loop from its original session."
        escaped_info=$(echo "$loop_info" | jq -Rs .)
        echo "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $escaped_info}}"
    else
        loop_warning="Active lc-build loop detected (universe: ${loop_universe}, iteration ${loop_iter:-?}/${loop_max:-?})
  Resume: /lc-build --universe ${loop_universe}    Cancel: /cancel-ralph"
        escaped_warning=$(echo "$loop_warning" | jq -Rs .)
        echo "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $escaped_warning}}"
    fi
    exit 0
fi

# Sync extraction model from ~/.lightcone/config.yaml to .claude/agents/lc-extractor.md
if [ -f ".claude/agents/lc-extractor.md" ] && [ -f "$HOME/.lightcone/config.yaml" ]; then
    ext_model=$(grep '^extraction_model:' "$HOME/.lightcone/config.yaml" 2>/dev/null | awk '{print $2}' | tr -d "'\"")
    # Default to sonnet if not configured
    [ -z "$ext_model" ] && ext_model="sonnet"
    if [ -n "$ext_model" ]; then
        if ! grep -q "^model:" .claude/agents/lc-extractor.md 2>/dev/null; then
            # Insert model field after description line
            sed -i.bak '/^tools:/i\
model: '"$ext_model" .claude/agents/lc-extractor.md 2>/dev/null && rm -f .claude/agents/lc-extractor.md.bak
        else
            # Update existing model field
            sed -i.bak 's/^model: .*/model: '"$ext_model"'/' .claude/agents/lc-extractor.md 2>/dev/null && rm -f .claude/agents/lc-extractor.md.bak
        fi
    else
        # Empty model = inherit, remove model line if present
        sed -i.bak '/^model: /d' .claude/agents/lc-extractor.md 2>/dev/null && rm -f .claude/agents/lc-extractor.md.bak
    fi
fi

# Check if this is an ASTRA project (has astra.yaml)
if [ ! -f "astra.yaml" ]; then
    exit 0
fi

# Check if astra command is available
if ! command -v astra &> /dev/null; then
    # Provide minimal info without CLI
    echo "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": \"This is an ASTRA project. The astra CLI is not installed - run 'pip install astra' to enable validation and other commands.\"}}"
    exit 0
fi

# Gather analysis information
analysis_name=$(grep -m1 "^  name:" astra.yaml 2>/dev/null | sed 's/.*name: *"\?\([^"]*\)"\?/\1/' | tr -d '"')

# Count decisions
decision_count=$(grep -c "^  [a-z_]*:$" astra.yaml 2>/dev/null | head -1)
# More accurate: count keys under 'decisions:'
decision_count=$(awk '/^decisions:/{found=1; next} found && /^  [a-z_]+:/{count++} found && /^[a-z]/{exit} END{print count}' astra.yaml 2>/dev/null)

# Count universes
universe_count=$(ls -1 universes/*.yaml 2>/dev/null | wc -l | tr -d ' ')

# Check validation status
validation_result=$(astra validate astra.yaml 2>&1)
if [ $? -eq 0 ]; then
    validation_status="valid"
else
    validation_status="has errors"
fi

# Build summary
summary="ASTRA Project: ${analysis_name:-unnamed}
- Decisions: ${decision_count:-0}
- Universes: ${universe_count:-0}
- Validation: ${validation_status}
- Reference: For astra.yaml syntax and spec format, read .claude/guides/astra-reference.md; for CLI and execution, read .claude/guides/lightcone-cli-reference.md"

# If validation failed, add error summary
if [ "$validation_status" = "has errors" ]; then
    # Get first few lines of errors
    error_preview=$(echo "$validation_result" | head -5)
    summary="$summary

Validation errors (run 'astra validate astra.yaml' for details):
$error_preview"
fi

# Add lc status if lc CLI is available
if command -v lc &> /dev/null; then
    lc_status=$(lc status 2>&1)
    lc_exit=$?
    if [ $lc_exit -eq 0 ]; then
        # Count outputs in each state
        pending_count=$(echo "$lc_status" | grep -c "pending")
        ok_count=$(echo "$lc_status" | grep -c "ok")
        no_recipe_count=$(echo "$lc_status" | grep -c "no recipe")

        summary="$summary

Materialization status:
- ok: ${ok_count}
- pending: ${pending_count}
- no recipe: ${no_recipe_count}"

        if [ "$pending_count" -gt 0 ]; then
            summary="$summary

ACTION REQUIRED: ${pending_count} output(s) have recipes but are not yet materialized. Use \`lc run\` to produce them."
        fi
    fi
fi

# Output as JSON
escaped_summary=$(echo "$summary" | jq -Rs .)
echo "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $escaped_summary}}"

exit 0
