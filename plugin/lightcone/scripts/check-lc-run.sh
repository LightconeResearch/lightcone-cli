#!/bin/bash
# PostToolUse hook: Warn when agent runs a Python script that has an integrated recipe
# Triggers on Bash commands matching "python". Uses astra.helpers for correct YAML parsing.
# Only fires on specific recipe matches — silent otherwise (no Tier 2 noise).
# Parses --universe from the command to check the targeted universe; falls back to baseline.

# Read JSON input from stdin
input=$(cat)

# Extract the command that was run
command=$(echo "$input" | jq -r '.tool_input.command // empty')

# Exit silently if no command
if [ -z "$command" ]; then
    exit 0
fi

# Quick filter: only care about commands containing "python"
if ! echo "$command" | grep -qE 'python[23]?\s'; then
    exit 0
fi

# Skip lc/astra commands
if echo "$command" | grep -qE '(lc|astra)\s'; then
    exit 0
fi

# Must be in an ASTRA project
if [ ! -f "astra.yaml" ]; then
    exit 0
fi

# Use Python + astra.helpers to extract recipe commands and match against the agent's command.
# This correctly handles all YAML variants (block, inline, sub-analyses).
# Outputs a warning message if a match is found, or nothing if no match.
msg=$(python3 -c "
import sys, os, re

try:
    from astra.helpers import load_yaml, get_outputs_with_recipes
    from lightcone.engine.status import get_output_status
    from pathlib import Path
except ImportError:
    sys.exit(0)

try:
    data = load_yaml('astra.yaml')
except Exception:
    sys.exit(0)

recipes = get_outputs_with_recipes(data)
if not recipes:
    sys.exit(0)

# Parse --universe from the agent's command; fall back to baseline
agent_cmd = sys.argv[1]
universe = 'baseline'
m = re.search(r'--universe[= ]\s*(\S+)', agent_cmd)
if m:
    universe = m.group(1)

# Get status for the targeted universe
try:
    status = get_output_status(Path('.'), universe)
except Exception:
    status = {}

if not status:
    sys.exit(0)

# Check if any recipe output is integrated (pending or materialized) in this universe
has_integrated = any(
    status.get(o.get('id', ''), '') in ('pending', 'materialized')
    for o in recipes
)
if not has_integrated:
    # No integrated recipes in this universe — Write & Debug phase
    sys.exit(0)

# Build map: normalized script path -> (output_id, status)
recipe_scripts = {}
for o in recipes:
    cmd = o.get('recipe', {}).get('command', '')
    out_id = o.get('id', '')
    out_status = status.get(out_id, 'no_recipe')
    if out_status not in ('pending', 'materialized'):
        continue
    # Extract .py path from recipe command
    for part in cmd.split():
        if part.endswith('.py'):
            normalized = part.lstrip('./')
            recipe_scripts[normalized] = (out_id, out_status)
            recipe_scripts[os.path.basename(normalized)] = (out_id, out_status)
            break

if not recipe_scripts:
    sys.exit(0)

# Check the agent's command against recipe scripts
# Split on && || ; to handle chained commands
subcmds = re.split(r'&&|\|\||;', agent_cmd)

for subcmd in subcmds:
    subcmd = subcmd.strip()
    # Extract .py path from this sub-command
    for token in subcmd.split():
        if token.endswith('.py'):
            agent_script = token.lstrip('./')
            match = recipe_scripts.get(agent_script) or recipe_scripts.get(os.path.basename(agent_script))
            if match:
                out_id, out_status = match
                if out_status == 'pending':
                    print(f'WARNING: You just ran the script for output \`{out_id}\` (status: pending in {universe}), which has an integrated recipe. Use \`lc run {out_id} --universe {universe}\` instead to ensure reproducibility.')
                elif out_status == 'materialized':
                    print(f'NOTE: Output \`{out_id}\` already has results in {universe} from \`lc run\`. If regenerating, use \`lc run {out_id} --universe {universe}\` to keep results reproducible.')
                sys.exit(0)
            break
    # Also handle python -m
    parts = subcmd.split()
    for i, p in enumerate(parts):
        if p == '-m' and i + 1 < len(parts):
            module_path = parts[i + 1].replace('.', '/') + '.py'
            match = recipe_scripts.get(module_path) or recipe_scripts.get(os.path.basename(module_path))
            if match:
                out_id, out_status = match
                if out_status == 'pending':
                    print(f'WARNING: You just ran the module for output \`{out_id}\` (status: pending in {universe}), which has an integrated recipe. Use \`lc run {out_id} --universe {universe}\` instead.')
                elif out_status == 'materialized':
                    print(f'NOTE: Output \`{out_id}\` already has results in {universe} from \`lc run\`. If regenerating, use \`lc run {out_id} --universe {universe}\`.')
                sys.exit(0)
            break
" "$command" 2>/dev/null)

# If Python produced a message, return it as hook context
if [ -n "$msg" ]; then
    escaped_msg=$(echo "$msg" | jq -Rs .)
    echo "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": $escaped_msg}}"
fi

exit 0
