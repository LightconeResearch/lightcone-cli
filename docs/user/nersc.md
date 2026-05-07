# Installing and Using lightcone-cli on NERSC

A practical guide for running [`lightcone-cli`](https://github.com/LightconeResearch/lightcone-cli) on Perlmutter. The CLI works the same as anywhere else, but the filesystem layout, container runtime, and SLURM submission have NERSC-specific quirks that are worth knowing about up front.

---

## 0. Agentic CLI

`lightcone-cli` is the execution layer of the `lightcone` project — it harnesses an agent-based CLI (currently Claude Code) to follow the `astra` standard while building and running an analysis. So the very first step, even before touching `lightcone-cli` itself, is to install the agent.

```bash
curl -fsSL https://claude.ai/install.sh | bash   # installs to ~/.local/bin/claude
```

Add `~/.local/bin` to your `PATH` if it isn't already, then verify and authenticate:

```bash
claude --version
claude                                      # first run prompts for login (claude.ai or API key)
```

Other install routes (npm, native package managers) are documented in the [Claude Code installation docs](https://docs.claude.com/en/docs/claude-code/setup).

---

## 1. Python

NERSC's `python` module gives you a ready-to-use Python distribution with `conda`, `pip`, and many common scientific packages already installed — no env creation needed for the basics:

```bash
module load python      # NERSC Python (3.11+); brings conda and pip onto PATH
```

That's enough for installing `lightcone-cli` on top. See [§2](#2-install-lightcone-cli).

> **When to create your own conda env.** The NERSC python module is shared and read-only — you can install user-level packages on top of it, but you can't pin a different Python version or guarantee dependency isolation. If you want either, create a conda env on top:
>
> ```bash
> module load python
> conda create -n your-env-name python=3.11 -y
> conda activate your-env-name
> ```
>
> This is also NERSC's [recommended path for `pip install`](https://docs.nersc.gov/development/languages/python/nersc-python/) when you need custom packages: pip-into-conda-env rather than pip-into-base.

> **Storage note.** Conda envs land under `~/.conda/envs/`. The Perlmutter home quota is 40 GB; for larger envs NERSC recommends installing to `/global/common/software/<project>/` instead. If you really want them on `$SCRATCH` (12-week purge!), move and symlink:
>
> ```bash
> conda deactivate
> mv ~/.conda/envs/your-env-name $SCRATCH/conda-envs/
> ln -s $SCRATCH/conda-envs/your-env-name ~/.conda/envs/your-env-name
> ```
>
> See [NERSC's Python guide](https://docs.nersc.gov/development/languages/python/nersc-python/) for the full storage strategy and [the `ln(1)` man page](https://man7.org/linux/man-pages/man1/ln.1.html) for the symlink syntax.

---

## 2. Install lightcone-cli

With the environment ready, install the package itself.

### Into NERSC's python module (no conda env)

The shared NERSC `python` module is read-only, so install with `--user` to land into your home dir's site-packages:

```bash
python -m pip install --user lightcone-cli
```

This drops the `lc` console script into `~/.local/bin/`. Make sure that's on your `PATH` (Perlmutter usually has this by default — check with `echo $PATH | tr : '\n' | grep .local/bin`).

If you already use [`uv`](https://docs.astral.sh/uv/) (NERSC doesn't ship it, but you can install it yourself with `curl -LsSf https://astral.sh/uv/install.sh | sh`), `uv tool install` is a cleaner alternative — it isolates `lc` in its own venv and drops the same `~/.local/bin/lc` wrapper:

```bash
uv tool install lightcone-cli
```

### Into a conda env

```bash
conda activate your-env-name
python -m pip install lightcone-cli
```

If you use `uv`:

```bash
uv pip install lightcone-cli
```

`astra-tools` is a transitive dependency, so a single `lightcone-cli` install pulls it in automatically.

### From source (contributor route)

If you want to track the latest commits or contribute back, clone the repo and install editably. This is **optional** — most users should stick with PyPI.

```bash
cd ~/.lightcone                              # or wherever you keep clones

git clone https://github.com/LightconeResearch/lightcone-cli.git
pip install -e ./lightcone-cli               # editable install, follows local edits
```

If you also want to hack on `astra-tools` itself, clone the `ASTRA` repo (the package is published to PyPI as `astra-tools` but the GitHub repo is named `ASTRA`):

```bash
git clone https://github.com/LightconeResearch/ASTRA.git
pip install -e ./ASTRA
```

For development work, add the dev extras:

```bash
pip install -e "./lightcone-cli[dev]"        # adds pytest, ruff, mypy
```

### One-time setup

```bash
lc setup
```

This creates `~/.lightcone/config.yaml` with a default container runtime of `auto`. You can pin the runtime later (see [§5](#5-running-on-compute-nodes) — Perlmutter compute nodes need `podman-hpc`).

### Verify

```bash
which lc                                     # should be inside your active env's bin/
lc --version
lc --help
```

---

## 3. Initialize a new project

Now you're ready to start working with it:

```bash
lc init your-analysis    # scaffolds a new folder with everything lightcone needs
cd your-analysis
claude                   # launch Claude Code inside the project
```

---

## 4. Start your research with lightcone!

Once Claude Code is open, you can use the lightcone skillset to start a fresh analysis or migrate one from existing code — all driven by natural-language prompts to the agent.

For example, to start from scratch:

```text
/lc-new Please sample a standard Gaussian distribution using numpy.
```

Or to migrate from existing code in another directory:

```text
/lc-migrate I have code that samples a standard Gaussian distribution using numpy at @../gaussian_sampling. Please create an analysis based on it.
```

After initialization, just keep talking to the agent in plain English about what you want to build next. Note that your job will all run on **login node**, see the next section on how to run jobs on computing node.

---

## 5. Running on compute nodes

Everything up to this point ran on a Perlmutter **login node** — fine for installation, scaffolding, and `lc status`, but anything heavy belongs on a compute node. Login nodes are shared and should not be abused.

### Pre-flight: pin the container runtime and build images

On Perlmutter, compute nodes ship `podman-hpc`. Pin it once in your global config:

```yaml
# ~/.lightcone/config.yaml
container:
  runtime: podman-hpc
```

Then build and migrate the images for your project on a login node (`lc build` runs `podman-hpc build` then `podman-hpc migrate`, which copies the image into the per-node container cache):

```bash
cd /path/to/your-analysis
lc build
```

See [Running on a Cluster → Pre-flight](cluster.md#pre-flight-pick-the-right-container-runtime) for the underlying mechanics.

### Interactive runs (agent-driven)

The agent (Claude Code) will invoke `lc run` for you when it decides recipes need to materialize — you don't call it directly. What you control is *where Claude Code is running*: it inherits whatever shell environment you started it from. To get the agent's `lc run` calls onto a compute node, start `claude` from inside a SLURM allocation:

```bash
salloc -A <your_project> -q interactive -C gpu --nodes=1 -t 00:30:00
# allocation drops you onto a compute node; from there:
cd /path/to/your-analysis
claude
```

Now anything the agent decides to run (`lc run`, scripts, etc.) executes on the allocated node, not the login node.

The `interactive` QoS on the GPU partition is appropriate for development. For longer or larger sessions, see [NERSC's queue policy reference](https://docs.nersc.gov/jobs/policy/).

### Unattended batch runs (no agent in the loop)

If you want to submit `lc run` as an unattended batch job — i.e., without Claude Code in the loop — that path also works. See [Running on a Cluster → A typical SLURM workflow](cluster.md#a-typical-slurm-workflow) for the generic `sbatch` template; on Perlmutter, the only addition is the `-A`/`-q` directives:

```bash
#!/bin/bash
#SBATCH -A <your_project>
#SBATCH -q regular
#SBATCH -C gpu
#SBATCH -N 4
#SBATCH -t 04:00:00

cd $SCRATCH/your-analysis
source ~/.conda/envs/your-env-name/bin/activate   # or your venv
lc run -j 16
```

> Note: this path runs `lc run` directly, not through the agent — useful for production sweeps where you've already nailed down the recipes interactively. The agent-driven flow above is the right tool for development.

### Storage gotcha: Snakemake state must live on `$SCRATCH`

`$HOME` and `/global/cfs/` are mounted on compute nodes via DVS, which silently ignores `flock()`. Snakemake (and any sane locking system) uses `flock`, so its `.snakemake/` directory and Dask spill files must go on Lustre (`$SCRATCH`), which honors `flock`. Otherwise you get intermittent silent rule-rerun loops or hangs.

`lc` redirects state automatically when it detects Perlmutter, so this usually just works. To pin explicitly per project:

```bash
lc init your-analysis --scratch '$SCRATCH'    # expands at run time, kept verbatim in config
```

Or after the fact, add to `<project>/.lightcone/lightcone.yaml`:

```yaml
scratch_root: $SCRATCH
```

`$SCRATCH` is purged on a 12-week rolling window, so for outputs you want to keep, copy or symlink to `/global/cfs/cdirs/<project>/`.

### Further reading

- [NERSC interactive jobs](https://docs.nersc.gov/jobs/interactive/) — `salloc` patterns and reservation queues
- [Perlmutter system overview](https://docs.nersc.gov/systems/perlmutter/) — node types and partitions

---

## 6. Common troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `lc: command not found` | Wrong env active | `which lc`; reinstall in the active env |
| `lc` runs but uses unexpected code | Two installs across two envs shadowing each other on `PATH` | `which lc` and uninstall the stale one |
| `ModuleNotFoundError: lightcone.cli.__main__` | Tried `python -m lightcone.cli` (the package isn't directly executable) | Use the `lc` console script |
| Snakemake locking errors / silent rule rerun loops | `.snakemake/` ended up on DVS-mounted storage | Set `scratch_root: $SCRATCH` in the project's `.lightcone/lightcone.yaml` |
| `ImportError: cannot import name 'resolve_analysis_tree' from 'astra.helpers'` | Stale `astra-tools` (pre-0.2.5) | `pip install -U astra-tools` |
| `PermissionError` reading another user's symlinked `results/` | Cross-user scratch path without group ACLs | Request access from the data owner, or copy the manifests you need into your own scratch |
| `pip install` hangs or times out on a compute node | Compute nodes have no public internet | Always install from a login node |

---

## 7. Updating

For source installs:

```bash
cd ~/.lightcone/lightcone-cli
git pull
pip install -e .                             # only needed if pyproject.toml changed
```

Editable installs auto-follow source edits — switching branches or pulling new commits is reflected immediately in `lc`. Re-run `pip install -e .` only when `pyproject.toml` adds a new dependency or changes the `[project.scripts]` table.

For PyPI installs:

```bash
pip install -U lightcone-cli astra-tools
```

---

## 8. Uninstalling

```bash
pip uninstall lightcone-cli                  # remove from the active env
rm -rf ~/.lightcone/lightcone-cli            # remove source clone (only for source installs)
# Keep ~/.lightcone/config.yaml and ~/.lightcone/targets/ unless you want to start fresh.
```
