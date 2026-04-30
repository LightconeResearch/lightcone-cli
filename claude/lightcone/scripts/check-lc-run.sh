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
    # No outputs with integrated recipes — still in Write & Debug phase.
    sys.exit(0)

# Parse --universe from the agent's command; fall back to baseline.
agent_cmd = sys.argv[1]
universe = 'baseline'
m = re.search(r'--universe[= ]\s*(\S+)', agent_cmd)
if m:
    universe = m.group(1)

# Build map: normalized script path -> output_id (any output with a recipe).
recipe_scripts = {}
for o in recipes:
    cmd = o.get('recipe', {}).get('command', '')
    out_id = o.get('id', '')
    for part in cmd.split():
        if part.endswith('.py'):
            normalized = part.lstrip('./')
            recipe_scripts[normalized] = out_id
            recipe_scripts[os.path.basename(normalized)] = out_id
            break

if not recipe_scripts:
    sys.exit(0)

# Match the agent's command against recipe scripts. Split on && || ; to
# handle chained commands; also handle 'python -m module.path'.
matched_id = None
for sub in re.split(r'&&|\|\||;', agent_cmd):
    parts = sub.strip().split()
    for token in parts:
        if token.endswith('.py'):
            agent_script = token.lstrip('./')
            matched_id = (
                recipe_scripts.get(agent_script)
                or recipe_scripts.get(os.path.basename(agent_script))
            )
            break
    if matched_id:
        break
    for i, p in enumerate(parts):
        if p == '-m' and i + 1 < len(parts):
            module_path = parts[i + 1].replace('.', '/') + '.py'
            matched_id = (
                recipe_scripts.get(module_path)
                or recipe_scripts.get(os.path.basename(module_path))
            )
            break
    if matched_id:
        break

if not matched_id:
    sys.exit(0)

# Look up the matched output's manifest-driven status. States are
# 'ok' / 'stale' / 'missing' / 'alias' (alias outputs aren't in 'recipes').
try:
    statuses = {
        s.output_id: s.status
        for s in get_output_status(Path('.'), universe_id=universe)
    }
except Exception:
    sys.exit(0)

out_status = statuses.get(matched_id)
if out_status in ('missing', 'stale'):
    print(
        f'WARNING: You ran the script for output \`{matched_id}\` directly '
        f'(status: {out_status} in {universe}), which has an integrated '
        f'recipe. Use \`lc run {matched_id} --universe {universe}\` instead '
        f'so a manifest is written and the result is reproducible.'
    )
elif out_status == 'ok':
    print(
        f'NOTE: Output \`{matched_id}\` already has current results in '
        f'{universe} from \`lc run\`. Running the script directly bypasses '
        f'the manifest — use \`lc run {matched_id} --universe {universe}\` '
        f'to regenerate reproducibly.'
    )
" "$command" 2>/dev/null)

# If Python produced a message, return it as hook context
if [ -n "$msg" ]; then
    escaped_msg=$(echo "$msg" | jq -Rs .)
    echo "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": $escaped_msg}}"
fi

exit 0
