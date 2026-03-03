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
- Validation: ${validation_status}"

# If validation failed, add error summary
if [ "$validation_status" = "has errors" ]; then
    # Get first few lines of errors
    error_preview=$(echo "$validation_result" | head -5)
    summary="$summary

Validation errors (run 'astra validate astra.yaml' for details):
$error_preview"
fi

# Add prism status if prism CLI is available
if command -v prism &> /dev/null; then
    prism_status=$(prism status 2>&1)
    prism_exit=$?
    if [ $prism_exit -eq 0 ]; then
        # Count outputs in each state
        pending_count=$(echo "$prism_status" | grep -c "pending")
        ok_count=$(echo "$prism_status" | grep -c "ok")
        no_recipe_count=$(echo "$prism_status" | grep -c "no recipe")

        summary="$summary

Materialization status:
- ok: ${ok_count}
- pending: ${pending_count}
- no recipe: ${no_recipe_count}"

        if [ "$pending_count" -gt 0 ]; then
            summary="$summary

ACTION REQUIRED: ${pending_count} output(s) have recipes but are not yet materialized. Use \`prism run\` to produce them."
        fi
    fi
fi

# Output as JSON
escaped_summary=$(echo "$summary" | jq -Rs .)
echo "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $escaped_summary}}"

exit 0
