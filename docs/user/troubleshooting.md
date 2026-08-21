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

## `lc run` on JupyterHub: "No Dask Gateway worker became ready within 600s"

`lc run` creates a Gateway cluster and waits for its first worker before
dispatching anything. If none appears, the run stops rather than hanging
forever at zero workers. In practice this is almost always **an image
the cluster can't pull**:

- Run `lc build` on its own and read the error — a failed Cloud Build
  shows up there much more clearly.
- Check the cluster's state in the JupyterLab Dask panel; a worker pod
  stuck in `ImagePullBackOff` confirms it.
- Less often it's capacity: the node pool has no room (or quota) to
  schedule a worker.
- If your image is genuinely huge and the first cold pull is just slow,
  raise the bound:
  ```bash
  export LIGHTCONE_GATEWAY_WORKER_TIMEOUT=1800
  ```

## "No image build backend on this host"

You're on the `kubernetes` runtime (no local docker/podman) and the
environment doesn't carry the Cloud Build contract — `LIGHTCONE_REGISTRY`
and `LIGHTCONE_BUILD_BUCKET` (plus the optional
`LIGHTCONE_BUILD_SERVICE_ACCOUNT`). On a lightcone JupyterHub these are
injected into every user pod, so a missing one means the deployment is
misconfigured; ask your hub admin. If you landed on the `kubernetes`
runtime by accident, pin a real one in `~/.lightcone/config.yaml`
(`container: {runtime: podman}`) or pass `lc build --runtime …`.

## A Cloud Build image build fails

`lc build` tails the failed build's log. Read it first — the common
causes are ordinary Dockerfile problems (a package that doesn't exist
for the base image, a `COPY` of a path that isn't in the project). Two
that aren't:

- **Permission errors from GCP.** The pod's identity needs
  `cloudbuild.builds.editor`, `iam.serviceAccountUser` on the build
  service account, object create/view on the build bucket, and
  `artifactregistry.reader` for the freshness check. A deployment issue,
  not a project one.
- **Nothing happens and it says the image is cached.** That's success:
  the content hash matched an image already in the registry, so there
  was nothing to build. Force it with `lc build --force`.

## `lc run` refuses: several container images on this deployment

On a JupyterHub deployment the worker pod *is* the container, so one run
executes in exactly one image. If `astra.yaml` declares more than one
distinct `container:` (root, sub-analysis, or recipe level), `lc run`
stops before starting and lists them.

Consolidate on a single Containerfile that has everything, or a single
shared prebuilt image. This restriction is specific to that backend —
locally and on SLURM each rule is wrapped individually and any number of
images is fine.

## Every rule hangs on JupyterHub, with no error

If `lc run` reports that workers "do not advertise the lightcone
resource contract", stop there: Dask only schedules a task on a worker
advertising *every* resource key the task requests (`cpus` for every
rule, `memory` for any rule with `mem_mb`), so a worker missing them
would sit idle forever.

`lc` provisions those keys through the gateway's standard `environment`
cluster option. Seeing this error means the deployment doesn't expose
that option, or strips it. Ask the hub admin to expose the standard
`image` / `worker_cores` / `worker_memory` / `environment` cluster
options.

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

## Claude Code says it can't write a file

The default permission tier (`recommended`) **blocks** edits to a few
sensitive places — `~/.ssh`, `~/.aws`, `~/.gnupg` — plus `sudo`,
`git push`, `rm -rf`.

It also **prompts** (rather than blocks) before writing under `/scratch`
or `/pscratch`: on HPC your project often lives there, so the agent
legitimately needs to write to it, but a stray edit would be expensive.
Approve the prompt and it proceeds.

If the file you're trying to edit isn't in either list, check
`.claude/settings.json`. If it's in the deny list — your `recommended`
tier is doing its job. Either move the work elsewhere or, knowing what
you're doing, invoke `lc init … --permissions yolo` next time.

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
