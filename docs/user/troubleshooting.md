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

`lc init` won't run inside an existing project (it refuses if
`astra.yaml` already exists).

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

## "Active lc-build loop detected"

You're picking up a session where a previous `/lc-build` was
interrupted. The session-start hook prints this in the banner. To
resume the loop, run `/lc-build --universe <name>`. To cancel it,
`/cancel-ralph`.

## The build loop runs forever / never says complete

`/lc-build` defaults to a 25-iteration cap. If it's not making
progress, that's a sign the analysis hit a real problem the agent
can't resolve on its own — typically a missing dependency, an
unparseable error, or a step that needs a human decision.

What helps:

- Read the last few iterations carefully — the agent usually
  describes the blocker.
- If there's an "open question" the agent flagged, answer it and
  reinvoke `/lc-build`. The plan file persists; the loop picks up
  where it left off.
- A `/clear` followed by `/lc-build` doesn't lose state — only
  context.

## Claude Code says it can't write a file

The default permission tier (`recommended`) blocks edits to a few
sensitive places: `~/.ssh`, `~/.aws`, `~/.gnupg`, `/scratch`,
`/pscratch`, plus `sudo`, `git push`, `rm -rf`, …

If the file you're trying to edit isn't in those, check
`.claude/settings.json`. If it is — your `recommended` tier is doing
its job. Either move the work elsewhere or, knowing what you're doing,
invoke `lc init … --permissions yolo` next time.

## I deleted `.claude/` by accident

`lc init` won't recreate it because `astra.yaml` exists. You can copy
the plugin in by hand:

```bash
python - <<'PY'
import shutil
from pathlib import Path
from lightcone.cli.plugin import get_plugin_source_dir
src = get_plugin_source_dir()
dst = Path(".claude")
for sub in ("skills", "agents", "scripts", "guides", "templates"):
    s, d = src / sub, dst / sub
    if d.exists(): shutil.rmtree(d)
    if s.exists(): shutil.copytree(s, d)
PY
```

## I want to start the spec over

Move `astra.yaml` aside (don't delete it — agents like having context
about what you tried), then `/lc-new` again:

```bash
mv astra.yaml astra.previous.yaml
claude
# /lc-new
```

## File a bug from inside the session

Inside Claude Code:

```text
/lc-feedback the lc-extractor agent crashed on PDF X
```

The skill files an issue with auto-collected versions and a trimmed
error trace. See [`/lc-feedback`](../skills/lc-feedback.md).

## When all else fails

Run `lc verify` — it's the fastest way to know whether your problem
is provenance (real problem) or a transient build/run issue (rerun).
