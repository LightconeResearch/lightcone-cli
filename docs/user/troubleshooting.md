# Troubleshooting

Common issues and how to unstick them. Roughly ordered by how often
they come up.

## "No global configuration found."

`~/.lightcone/config.yaml` is normally created automatically on first
use, but it may be missing if the home directory was unavailable or if
the file was deleted manually. Re-create it by hand:

```bash
mkdir -p ~/.lightcone
cat > ~/.lightcone/config.yaml <<'EOF'
container:
  runtime: auto
EOF
```

Or just run any `lc` command (e.g. `lc --version`) — the auto-creation
runs before every command.

## "No astra.yaml found in current directory or any parent."

You're outside an ASTRA project. Either:

```bash
cd path/to/your/project
```

or, if you're starting fresh:

```bash
lc init my-analysis
cd my-analysis
```

`lc init` is convergent — it adds whatever artifact is missing and leaves
whatever is present alone, so it is safe to run in a new or existing
directory. It never rewrites `astra.yaml`. See
[The upgrade model](upgrading.md).

## "lc: command not found" or `lc` prints a directory listing

Two possibilities:

1. The package isn't installed for your current Python. Check
   `pip show lightcone-cli` (or `uv pip show lightcone-cli`).
2. Your shell has a personal alias `lc='ls --color'` shadowing the
   real command. Run `type lc` to see; `unalias lc` to remove.

## `lc run` warning: "No container runtime found on PATH"

You declared a container in `astra.yaml` but `auto` couldn't find any
of `docker`, `podman`, or `podman-hpc`. Two options:

- **Install one.** Podman is the smallest install on Linux and macOS.
- **Opt out explicitly.** Edit `~/.lightcone/config.yaml`:
  ```yaml
  container:
    runtime: none
  ```
  This silences the warning, but then your manifests will record an
  image that didn't actually run — fine for development, not fine for
  archival.

## `lc run` says "Workflow defines that rule … but no input"

This is Snakemake speak. It usually means:

- A recipe declares `inputs: [foo]` but no other output produces
  `foo`. Either the input is external (in which case it shouldn't be
  in the recipe's `inputs:` list — recipes only chain to *sibling*
  outputs), or there's a typo.
- Sub-analysis output ids that collide with root output ids — qualify
  with `<analysis_id>.<output_id>`.

The fix is in `astra.yaml`. `astra validate astra.yaml` will catch
most typos.

## `lc status` shows everything `stale` after I just ran

Something in the spec changed in a way that affects `code_version`.
That hash covers recipe text, container image identifier, and
decisions. Common causes:

- You edited a `Containerfile` or a dependency file (`requirements.txt`,
  `pyproject.toml`). The image's content-addressed tag changed →
  every recipe that uses it is now `stale`.
- You edited a recipe `command:`. Just rerun.
- You changed the default for a decision.

Re-running `lc run` will bring everything back to `ok`.

## `lc verify` fails with `tampered_data`

The bytes in an output directory no longer hash to the recorded
`data_version`. Most innocent cause: someone hand-edited a result
file. Most concerning: results were forged.

If it was you, regenerate with `lc run --force <output>`. If it
wasn't you, audit your shared filesystem.

## `lc verify` fails with `broken_chain`

A downstream output was materialized against an upstream version that
no longer exists. Usually caused by:

- The upstream was rerun without rerunning the downstream.
- The upstream's output directory was edited by hand (which would also
  trigger `tampered_data` on the upstream itself).

Fix: `lc run` the downstream output. The chain will re-anchor.

## Recommended permissions for cluster work

`lc init` does not write a permission policy. Permissions belong to the
harness. You choose the trust level your agent runs under. This guidance is
offered, not imposed.

For frictionless operation with Claude Code, run it in "accept edits" mode.
Cycle to it with `shift+tab`, start with `--permission-mode acceptEdits`, or
set `permissions.defaultMode` in `.claude/settings.json`. Auto mode carries the
same idea further for a loop you trust.

With Codex, pick the "Approve for me" mode (a.k.a. Auto-review, config key
`approvals_reviewer = "auto_review"`). It reviews and approves routine actions
for you, so the loop keeps moving.

On a shared cluster you may want guardrails instead. The rules below are a
starting point, not a requirement. Copy them into `.claude/settings.json` (or
your user `~/.claude/settings.json`) and adjust. The `ask` rules prompt before
a write to a scratch filesystem, where a stray edit can trash another user's
data. The `deny` rules block edits to secrets and a few dangerous commands.

```json
{
  "permissions": {
    "allow": ["Read", "Edit", "Write", "Bash(*)", "WebSearch", "WebFetch"],
    "ask": [
      "Edit(//scratch/**)",
      "Edit(//pscratch/**)",
      "Write(//scratch/**)",
      "Write(//pscratch/**)"
    ],
    "deny": [
      "Edit(~/.ssh/**)",
      "Edit(~/.aws/**)",
      "Edit(~/.gnupg/**)",
      "Bash(sudo *)",
      "Bash(rm -rf *)",
      "Bash(rm -fr *)",
      "Bash(git push *)",
      "Bash(git push)"
    ]
  }
}
```

A future "best-practices" skill may carry this guidance so you do not have to
copy it by hand.

## The agent says it can't write a file

Check your harness permission settings for a `deny` or `ask` rule that matches
the path (for Claude Code, `.claude/settings.json`). `lc init` writes no
permission rules, so any block comes from your own settings or your harness
trust level. Either move the work elsewhere or relax the rule.

## I deleted `.claude/settings.json` by accident

This file activates the `lightcone` plugin in the project. Run `lc init` inside
the project. It is convergent: it re-adds the activation non-destructively and
leaves `astra.yaml` alone. Or write the file back by hand — it enables the plugin
that the global marketplace registration provides:

```json
{
  "enabledPlugins": {"lightcone@lightcone-research": true}
}
```

Then restart Claude Code and trust the folder so it loads the plugin. (If the
marketplace registration is gone too, re-add it globally with
`claude plugin marketplace add LightconeResearch/agent-skills`. On Codex,
re-register the plugin with `codex plugin add lightcone@lightcone-research`.)

## I want to start the spec over

Move `astra.yaml` aside (don't delete it — agents like having context
about what you tried), then `/lightcone:new` again:

```bash
mv astra.yaml astra.previous.yaml
claude   # or your agent CLI of choice
# /lightcone:new
```

## File a bug from inside the session

Inside your agent session:

```text
/lightcone:feedback the lc-extractor agent crashed on PDF X
```

The skill files an issue with auto-collected versions and a trimmed
error trace. See [Skills](../skills/index.md).

## When all else fails

Run `lc verify` — it's the fastest way to know whether your problem
is provenance (real problem) or a transient build/run issue (rerun).
