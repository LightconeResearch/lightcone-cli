#!/bin/bash
# PostToolUse hook: warn when the agent runs a Python script that has an
# integrated recipe — they should use `lc run` so a manifest gets written.
#
# Pure bash + jq + the `lc` binary on PATH. No Python imports — the hook
# previously imported `lightcone.engine.status` which silently failed in
# the empty venv `lc init` creates, so the hook was a no-op for nearly
# every project. Status data now comes from `lc status --json`, which
# carries the recipe template per output as of the same change.

input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // empty')

[ -z "$command" ] && exit 0

# Only inspect commands that contain "python" / "python3"
echo "$command" | grep -qE 'python[23]?[[:space:]]' || exit 0

# Skip lc/astra invocations (they're already going through the right path)
echo "$command" | grep -qE '(^|[[:space:]/])(lc|astra)[[:space:]]' && exit 0

# Must be in an ASTRA project
[ -f "astra.yaml" ] || exit 0

# Must have lc on PATH; otherwise we can't get status
command -v lc &> /dev/null || exit 0

# Parse --universe NAME or --universe=NAME from the agent's command;
# default to baseline.
universe=$(echo "$command" \
  | grep -oE -- '--universe[= ][[:space:]]*[^[:space:]]+' \
  | head -1 \
  | sed -E 's/--universe[= ][[:space:]]*//')
[ -z "$universe" ] && universe="baseline"

status_json=$(lc status --json --universe "$universe" 2>/dev/null) || exit 0

# Collect script paths the agent invoked. Split on && || ; first so each
# chained command is considered separately, then look for tokens ending
# in .py and `python -m module.path`.
agent_scripts=$(echo "$command" | awk '
{
    # Split on shell separators
    gsub(/&&|\|\||;/, "\n")
    print
}' | awk '
{
    n = split($0, tok, /[[:space:]]+/)
    for (i = 1; i <= n; i++) {
        t = tok[i]
        if (t ~ /\.py$/) {
            sub(/^\.\//, "", t)
            print t
            n2 = split(t, pp, "/")
            print pp[n2]
        }
        if (t == "-m" && i + 1 <= n) {
            mod = tok[i+1]
            gsub(/\./, "/", mod)
            print mod ".py"
            n2 = split(mod, pp, "/")
            print pp[n2] ".py"
        }
    }
}' | sort -u)

[ -z "$agent_scripts" ] && exit 0

# For each output that has a recipe, extract the script path from the
# recipe template and check whether the agent's command invoked it.
matched=$(echo "$status_json" | jq -r --arg uni "$universe" '
    .universes[]
    | select(.universe_id == $uni)
    | .outputs[]
    | select(.recipe_command != null and .recipe_command != "")
    | "\(.output_id)\t\(.status)\t\(.recipe_command)"
' | while IFS=$'\t' read -r out_id out_status recipe_cmd; do
    recipe_scripts=$(echo "$recipe_cmd" | tr ' \t\n' '\n' | grep '\.py$' | sed 's|^\./||')
    for r in $recipe_scripts; do
        rb=$(basename "$r")
        if echo "$agent_scripts" | grep -qFx "$r" || echo "$agent_scripts" | grep -qFx "$rb"; then
            printf '%s\t%s\n' "$out_id" "$out_status"
            break
        fi
    done
done | head -1)

[ -z "$matched" ] && exit 0

matched_id=$(echo "$matched" | cut -f1)
matched_status=$(echo "$matched" | cut -f2)

case "$matched_status" in
    missing|stale)
        msg="WARNING: You ran the script for output \`$matched_id\` directly (status: $matched_status in $universe), which has an integrated recipe. Use \`lc run $matched_id --universe $universe\` instead so a manifest is written and the result is reproducible."
        ;;
    ok)
        msg="NOTE: Output \`$matched_id\` already has current results in $universe from \`lc run\`. Running the script directly bypasses the manifest — use \`lc run $matched_id --universe $universe\` to regenerate reproducibly."
        ;;
    *)
        exit 0
        ;;
esac

escaped_msg=$(echo "$msg" | jq -Rs .)
echo "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": $escaped_msg}}"
exit 0
