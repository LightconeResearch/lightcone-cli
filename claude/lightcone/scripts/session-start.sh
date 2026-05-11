#!/bin/bash
# SessionStart hook: surface a terse project status to the agent.
#
# Reports validation status, materialization counts, and a tight CLI
# primer so the agent knows what substrate commands exist and which
# reference skills carry the depth. Project name / decision count /
# universe count are intentionally omitted -- they are trivia the agent
# reads from astra.yaml and CLAUDE.md when needed, and they cost against
# the 10k additionalContext budget.

input=$(cat)
cwd=$(echo "$input" | jq -r '.cwd // empty')

[ -z "$cwd" ] && exit 0
cd "$cwd" 2>/dev/null || exit 0
[ -f "astra.yaml" ] || exit 0

# astra and lc both come from the project venv (prepended to PATH by
# activate-venv.sh). If neither resolved, the venv setup is broken and
# there is nothing useful we can report.
command -v astra &>/dev/null || exit 0
command -v lc &>/dev/null || exit 0

validation_output=$(astra validate astra.yaml 2>&1)
validation_ok=$?

status_json=$(lc status --json 2>/dev/null)
counts=$(echo "$status_json" | jq -r '
    [.universes[].outputs[].status] as $s |
    {
        ok: ($s | map(select(. == "ok")) | length),
        stale: ($s | map(select(. == "stale")) | length),
        missing: ($s | map(select(. == "missing")) | length),
        alias: ($s | map(select(. == "alias")) | length)
    } | "\(.ok) \(.stale) \(.missing) \(.alias)"
' 2>/dev/null)
read -r ok_count stale_count missing_count alias_count <<<"$counts"
ok_count=${ok_count:-0}
stale_count=${stale_count:-0}
missing_count=${missing_count:-0}
alias_count=${alias_count:-0}

if [ "$validation_ok" -eq 0 ]; then
    summary="ASTRA project — validation: valid"
else
    summary="ASTRA project — validation: has errors"
fi

summary="$summary
Materialization: ok=$ok_count stale=$stale_count missing=$missing_count alias=$alias_count

Substrate CLIs (use --help on any):
  lc init / lc run / lc status / lc verify / lc build / lc export wrroc
  astra validate / astra paper add / astra universe generate

Reference skills (invoke when the surface above isn't enough):
  /astra   — astra.yaml spec: decisions, prior_insights, findings, evidence, sub-analyses, narrative anchors
  /lc-cli  — lc workflow: spec-code invariant, status interpretation, failure diagnosis"

if [ "$validation_ok" -ne 0 ]; then
    # tail rather than head -- the leading lines are success markers
    # ("✓ Schema validation passed" etc.) and the actual error block is
    # at the bottom.
    error_preview=$(echo "$validation_output" | tail -20)
    summary="$summary

Validation errors (run 'astra validate astra.yaml' for full output):
$error_preview"
fi

needs_run=$((missing_count + stale_count))
if [ "$needs_run" -gt 0 ]; then
    summary="$summary

ACTION REQUIRED: $needs_run output(s) need \`lc run\` ($missing_count missing, $stale_count stale)."
fi

jq -n --arg ctx "$summary" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
exit 0
