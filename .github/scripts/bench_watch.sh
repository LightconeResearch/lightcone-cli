#!/usr/bin/env bash
# Copy to lightcone-cli:.github/scripts/bench_watch.sh
# Watch the lightcone-bench run dispatched for a PR commit and keep ONE PR comment
# (found by the marker below) up to date: the run link while it runs, the outcome
# when it ends. A dispatch returns no run id, so the run is found by the sha in its
# title — the bench workflow's run-name carries stack_ref for exactly this reason.
#
# When the run ends, the comment also carries the run's "Overall" row (all agents
# pooled: mean reward, mean steps/tokens/cost/time, total cost), read from the
# `overall` block of the run's results.json artifact.
#
# Env: BENCH_TOKEN  reads runs in lightcone-bench (the dispatch token)
#      GH_TOKEN     comments on this repo (the job's GITHUB_TOKEN, pull-requests: write)
#      REPO         owner/name of this repo      PR   pull request number
#      SHA          head sha being benchmarked   POLL_SECONDS (default 600)
#      MAX_POLLS    give up after this many polls (default 18 → 3 h)
set -uo pipefail
BENCH=LightconeResearch/lightcone-bench
API=https://api.github.com
MARKER="<!-- lightcone-bench-run -->"
POLL_SECONDS="${POLL_SECONDS:-600}"
MAX_POLLS="${MAX_POLLS:-18}"
short="${SHA:0:7}"

bench_api() { curl -sf -H "Authorization: Bearer $BENCH_TOKEN" -H "Accept: application/vnd.github+json" "$@"; }
repo_api()  { curl -sf -H "Authorization: Bearer $GH_TOKEN"    -H "Accept: application/vnd.github+json" "$@"; }

upsert_comment() { # $1 = markdown body; creates the marker comment or edits it in place
  local payload id
  payload=$(jq -n --arg m "$MARKER" --arg b "$1" '{body: ($m + "\n" + $b)}')
  id=$(repo_api "$API/repos/$REPO/issues/$PR/comments?per_page=100" \
       | jq -r --arg m "$MARKER" '[.[] | select(.body | startswith($m))] | first | .id // empty')
  if [ -n "$id" ]; then
    repo_api -X PATCH "$API/repos/$REPO/issues/comments/$id" -d "$payload" >/dev/null
  else
    repo_api -X POST "$API/repos/$REPO/issues/$PR/comments" -d "$payload" >/dev/null
  fi
}

overall_table() { # markdown table of the run's pooled results; empty if unavailable
  local url dir
  url=$(bench_api "$API/repos/$BENCH/actions/runs/$run_id/artifacts?name=results" \
        | jq -r '.artifacts[0].archive_download_url // empty')
  [ -n "$url" ] || return 0
  dir=$(mktemp -d)
  curl -sfL -H "Authorization: Bearer $BENCH_TOKEN" "$url" -o "$dir/results.zip" \
    && unzip -qo "$dir/results.zip" results.json -d "$dir" || return 0
  jq -r '.overall // empty | select(.trials > 0)
    | def f(d): if . == null then "—" else (. * pow(10; d) | round / pow(10; d) | tostring) end;
      "| trials | scored | mean reward | steps | tool calls | shell calls | out tokens (k) | cached % | cost $ | agent min | total cost $ |",
      "|---|---|---|---|---|---|---|---|---|---|---|",
      "| \(.trials) | \(.scored) | \(.mean_reward | f(2)) | \(.agent_steps | f(0)) | \(.tool_calls_total | f(0)) | \(.shell_calls | f(0)) | \(.completion_ktokens | f(1)) | \(.cached_pct | f(0)) | \(.cost_usd | f(3)) | \(.agent_min | f(1)) | \(.total_cost_usd | f(3)) |"' \
    "$dir/results.json"
}

# 1. Find the run: it shows up a few seconds after the dispatch.
run_id=""; run_url=""
for _ in $(seq 1 12); do
  sleep 5
  read -r run_id run_url < <(bench_api "$API/repos/$BENCH/actions/workflows/benchmark.yml/runs?event=workflow_dispatch&per_page=30" \
    | jq -r --arg sha "$SHA" '[.workflow_runs[] | select(.display_title | contains($sha))]
                              | sort_by(.created_at) | last | select(. != null) | "\(.id) \(.html_url)"')
  [ -n "$run_id" ] && break
done
if [ -z "$run_id" ]; then
  upsert_comment "**lightcone-bench** — dispatched a run for \`$short\` but could not find it in the queue; look under [lightcone-bench → Actions](https://github.com/$BENCH/actions/workflows/benchmark.yml)."
  exit 0
fi
upsert_comment "**lightcone-bench** — benchmarking \`$short\`: [run $run_id]($run_url) ⏳ in progress (checked every $((POLL_SECONDS / 60)) min)."

# 2. Wait for it, editing the same comment when it ends.
for _ in $(seq 1 "$MAX_POLLS"); do
  sleep "$POLL_SECONDS"
  read -r status conclusion < <(bench_api "$API/repos/$BENCH/actions/runs/$run_id" | jq -r '"\(.status) \(.conclusion)"')
  [ "${status:-}" = "completed" ] || continue
  case "$conclusion" in
    success)   icon="✅"; word="passed" ;;
    cancelled) icon="⚪"; word="was cancelled" ;;
    *)         icon="❌"; word="failed ($conclusion)" ;;
  esac
  body="**lightcone-bench** — benchmark of \`$short\` $icon $word: [run $run_id]($run_url) — the per-agent tables, baseline deltas and trace report are in its job summary."
  table=$(overall_table)
  [ -n "$table" ] && body="$body"$'\n\n'"All agents pooled (reward averaged over scored trials, the rest over all trials):"$'\n\n'"$table"
  upsert_comment "$body"
  exit 0
done
upsert_comment "**lightcone-bench** — benchmark of \`$short\` is still running after $((MAX_POLLS * POLL_SECONDS / 3600)) h: [run $run_id]($run_url)."
