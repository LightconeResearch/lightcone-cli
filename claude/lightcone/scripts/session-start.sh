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

# Gather analysis information. `astra init` writes `name:` at the top
# level (no indent); previous indented form was pre-asset-centric.
# Use -E (ERE) so `?` works on both BSD and GNU sed.
analysis_name=$(grep -m1 "^name:" astra.yaml 2>/dev/null | sed -E 's/^name:[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/')

# Count keys under 'decisions:' (block-form decisions only)
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

# If validation failed, add error summary. We prefer the tail because the
# leading lines are the success header (`✓ Schema validation passed` etc.)
# and the actual error block is at the bottom. `head -5` historically hid
# every real error.
if [ "$validation_status" = "has errors" ]; then
    error_preview=$(echo "$validation_result" | tail -20)
    summary="$summary

Validation errors (run 'astra validate astra.yaml' for full output):
$error_preview"
fi

# Add lc status if lc CLI is available. States are 'ok' / 'stale' /
# 'missing' / 'alias' (manifest-driven). The output uses Rich glyphs:
# '✓ ok', '✸ stale', '✗ miss', '→ alias'.
if command -v lc &> /dev/null; then
    lc_status=$(lc status 2>&1)
    lc_exit=$?
    if [ $lc_exit -eq 0 ]; then
        ok_count=$(echo "$lc_status" | grep -c "✓ ok")
        stale_count=$(echo "$lc_status" | grep -c "✸ stale")
        missing_count=$(echo "$lc_status" | grep -c "✗ miss")
        alias_count=$(echo "$lc_status" | grep -c "→ alias")

        summary="$summary

Materialization status:
- ok: ${ok_count}
- stale: ${stale_count}
- missing: ${missing_count}
- alias: ${alias_count}"

        needs_run=$((missing_count + stale_count))
        if [ "$needs_run" -gt 0 ]; then
            summary="$summary

ACTION REQUIRED: ${needs_run} output(s) need \`lc run\` (${missing_count} missing, ${stale_count} stale)."
        fi
    fi
fi

# Output as JSON
escaped_summary=$(echo "$summary" | jq -Rs .)
echo "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": $escaped_summary}}"

exit 0
