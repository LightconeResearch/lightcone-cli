# lightcone-cli on NERSC (Perlmutter)

A practical guide for running [`lightcone-cli`](https://github.com/LightconeResearch/lightcone-cli) on **Perlmutter**. The CLI itself behaves the same as on a laptop — the wrinkles are in the filesystem layout (DVS-mounted home, Lustre scratch), the container runtime (`podman-hpc`), and SLURM submission. This page covers all three.

!!! tip "Already familiar with the basics?"
    The generic [Install](install.md) and [Running on a Cluster](cluster.md) pages cover the cross-platform story. This page is the NERSC-specific overlay — read it first if Perlmutter is your home base.

---

## 0. Agentic CLI

`lightcone-cli` is the execution layer of the `lightcone` project — it harnesses an **agent-based CLI** to follow the `astra` standard while building and running an analysis. The choice of agent is open: anything that can drive a project shell works. This guide uses Claude Code as the running example — substitute your preferred agent CLI throughout if you use a different one.

Installing Claude Code:

```bash
curl -fsSL https://claude.ai/install.sh | bash   # installs to ~/.local/bin/claude
```

Make sure `~/.local/bin` is on your `PATH`, then verify and authenticate:

```bash
claude --version
claude                                           # first run prompts for login (claude.ai or API key)
```

Other install routes (npm, native package managers) are documented in the [Claude Code installation docs](https://docs.claude.com/en/docs/claude-code/setup).

!!! note "Other agent CLIs"
    Other agentic CLIs work too — for example:

    - [OpenAI Codex](https://github.com/openai/codex) — see the repo README for install options.
    - [opencode](https://opencode.ai/docs/#install) — install via `curl -fsSL https://opencode.ai/install | bash`.

    Pick whichever you prefer; the rest of this guide writes `claude` in concrete commands, but the workflow is the same with any agent CLI.

---

## 1. Python

Like the generic [Install](install.md#1-python) page, we recommend [`uv`](https://docs.astral.sh/uv/) for managing Python on Perlmutter — it's faster than pip and gives you a Python independent of NERSC's `module` system. NERSC doesn't ship it, but it installs into your home dir with a single curl:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

This drops both `uv` and an isolated Python 3.12 under `~/.local/`. Make sure `~/.local/bin` is on your `PATH`.

!!! note "Alternative: NERSC's `python` module"
    If you'd rather use NERSC's pre-built environment, `module load python` gives you a ready-to-use distribution with `conda`, `pip`, and many scientific packages already installed:

    ```bash
    module load python      # NERSC Python (3.11+); brings conda and pip onto PATH
    ```

    Convenient, but the module is shared and read-only — you can't pin a different Python version or guarantee dependency isolation. For that, build a conda env on top:

    ```bash
    conda create -n your-env-name python=3.11 -y
    conda activate your-env-name
    ```

    This is also NERSC's [recommended path for `pip install`](https://docs.nersc.gov/development/languages/python/nersc-python/) when you need custom packages.

!!! warning "Storage note: 40 GB home quota"
    Conda envs land under `~/.conda/envs/` by default. The Perlmutter home quota is **40 GB**, which gets eaten quickly. NERSC recommends `/global/common/software/<project>/` for larger envs. If you really want them on `$SCRATCH` (note: 12-week purge!), move and symlink:

    ```bash
    conda deactivate
    mv ~/.conda/envs/your-env-name $SCRATCH/conda-envs/
    ln -s $SCRATCH/conda-envs/your-env-name ~/.conda/envs/your-env-name
    ```

    See [NERSC's Python guide](https://docs.nersc.gov/development/languages/python/nersc-python/) for the full storage strategy.

---

## 2. Install lightcone-cli

The package on PyPI is `lightcone-cli`; the command it provides is `lc`. The recommended install uses `uv tool`, which isolates `lc` in its own venv under `~/.local/share/uv/tools/` and exposes a wrapper at `~/.local/bin/lc`:

```bash
uv tool install lightcone-cli
```

`astra-tools` is a transitive dependency — pulled in automatically.

!!! note "Alternative: pip"
    If you'd rather not use uv, install with pip. The exact command depends on which Python you're using:

    === "NERSC python module"
        ```bash
        module load python
        python -m pip install --user lightcone-cli   # --user lands in ~/.local/bin/
        ```

    === "Conda env"
        ```bash
        conda activate your-env-name
        python -m pip install lightcone-cli
        ```

### From source (contributors only)

If you want to track the latest commits or contribute back, clone the repo and install editably. **Most users should stick with PyPI.**

```bash
cd ~/.lightcone                                # or wherever you keep clones
git clone https://github.com/LightconeResearch/lightcone-cli.git
uv pip install -e ./lightcone-cli              # or: pip install -e ./lightcone-cli
```

To hack on `astra-tools` itself (PyPI name `astra-tools`, GitHub repo `ASTRA`):

```bash
git clone https://github.com/LightconeResearch/ASTRA.git
uv pip install -e ./ASTRA
```

For development tooling (pytest, ruff, mypy):

```bash
uv pip install -e "./lightcone-cli[dev]"
```

### Sanity check

```bash
which lc            # should resolve under ~/.local/bin/ or your active env
lc --version
lc --help
```

!!! note "Global config is auto-created"
    The first `lc` invocation writes `~/.lightcone/config.yaml` with `runtime: auto` — no manual setup step needed. You'll pin it to `podman-hpc` for compute nodes in [§5](#5-running-on-compute-nodes).

---

## 3. Initialize a new project

Scaffold a project directory and drop into it with the agent:

```bash
lc init your-analysis      # scaffolds a fresh project tree
cd your-analysis
claude                     # launch your agent CLI (Claude Code shown here)
```

---

## 4. Start your research

Once your agent CLI is open (Claude Code in this guide's examples), drive everything from there. The `lc-*` skills are how you tell the agent what to build:

=== "Start fresh"
    ```text
    /lc-new Please sample a standard Gaussian distribution using numpy.
    ```

=== "Migrate existing code"
    ```text
    /lc-migrate I have code that samples a standard Gaussian distribution using numpy at @../gaussian_sampling. Please create an analysis based on it.
    ```

After that, just keep talking to the agent in plain English about what you want to build next.

!!! warning "You're still on a login node"
    Everything from `lc init` through your first `/lc-new` runs on a Perlmutter **login node**. That's fine for scaffolding and small recipes, but anything heavyweight needs a compute node — see [§5](#5-running-on-compute-nodes).

---

## 5. Running on compute nodes

Login nodes are shared and rate-limited — fine for `lc init`, `lc status`, and small `lc build` calls, but anything heavyweight belongs on a compute node.

### Pre-flight: pin the container runtime and build images

Perlmutter compute nodes ship `podman-hpc`. Pin it once globally:

```yaml
# ~/.lightcone/config.yaml
container:
  runtime: podman-hpc
```

Then, on a login node, build and migrate your project's images:

```bash
cd /path/to/your-analysis
lc build
```

`lc build` runs `podman-hpc build` followed by `podman-hpc migrate`, which copies the image into each compute node's local container cache. See [Running on a Cluster → Pre-flight](cluster.md#pre-flight-pick-the-right-container-runtime) for the underlying mechanics.

### Interactive runs (agent-driven)

The agent calls `lc run` for you whenever a recipe needs to materialize — you never call it directly. What you *do* control is **where the agent is running**: it inherits the shell environment you launched it from. To put the agent's recipes onto a compute node, simply launch it from inside a SLURM allocation:

```bash
salloc -A <your_project> -q interactive -C gpu --nodes=1 -t 00:30:00
# salloc drops you onto a compute node; from there:
cd /path/to/your-analysis
claude                                            # or whichever agent CLI you use
```

Now everything the agent triggers (`lc run`, scripts, etc.) executes on the allocated node.

!!! note "Picking a QoS"
    The `interactive` QoS on the GPU partition is right for development. For longer or larger sessions, see [NERSC's queue policy reference](https://docs.nersc.gov/jobs/policy/).

### Unattended batch runs (no agent in the loop)

For production sweeps where the recipes are already nailed down, you can submit `lc run` directly as a batch job — no agent CLI involved. See [Running on a Cluster → A typical SLURM workflow](cluster.md#a-typical-slurm-workflow) for the generic template; on Perlmutter, the only addition is the `-A` / `-q` directives:

```bash
#!/bin/bash
#SBATCH -A <your_project>
#SBATCH -q regular
#SBATCH -C gpu
#SBATCH -N 4
#SBATCH -t 04:00:00

cd $SCRATCH/your-analysis

# Make `lc` available — pick the line that matches your install:
export PATH=$HOME/.local/bin:$PATH                # uv tool install (default)
# source ~/.conda/envs/your-env-name/bin/activate # conda env

lc run -j 16
```

!!! note "When to use this path"
    The agent-driven flow above is the right tool during development. Reach for batch submission when you've finished iterating and want a hands-off sweep.

### Storage gotcha: Snakemake state must live on `$SCRATCH`

!!! danger "DVS silently ignores `flock()`"
    `$HOME` and `/global/cfs/` are mounted on compute nodes via DVS, which silently ignores `flock()`. Snakemake (and any sane locking system) relies on `flock`, so its `.snakemake/` directory and Dask spill files **must** live on Lustre (`$SCRATCH`), which honors `flock`. Otherwise you get intermittent silent rule-rerun loops or hangs.

`lc` redirects state automatically when it detects Perlmutter, so this usually just works. To pin explicitly at project creation:

```bash
lc init your-analysis --scratch '$SCRATCH'        # kept verbatim, expanded at run time
```

Or, after the fact, edit `<project>/.lightcone/lightcone.yaml`:

```yaml
scratch_root: $SCRATCH
```

!!! warning "12-week purge on `$SCRATCH`"
    Perlmutter purges `$SCRATCH` on a rolling 12-week window. For outputs you need to keep, copy or symlink to `/global/cfs/cdirs/<project>/`.

### Further reading

- [NERSC interactive jobs](https://docs.nersc.gov/jobs/interactive/) — `salloc` patterns and reservation queues
- [Perlmutter system overview](https://docs.nersc.gov/systems/perlmutter/) — node types and partitions
- [NERSC Python guide](https://docs.nersc.gov/development/languages/python/nersc-python/) — module, conda, and pip layering

---

## 6. Common troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `lc: command not found` | Wrong env active, or `~/.local/bin` not on `PATH` | `which lc`; reinstall in the active env, or fix `PATH` |
| `lc` runs but uses unexpected code | Two installs across two envs shadowing each other on `PATH` | `which lc` and uninstall the stale one |
| `ModuleNotFoundError: lightcone.cli.__main__` | Tried `python -m lightcone.cli` (the package isn't directly executable) | Use the `lc` console script instead |
| Snakemake locking errors / silent rule rerun loops | `.snakemake/` ended up on DVS-mounted storage | Set `scratch_root: $SCRATCH` in the project's `.lightcone/lightcone.yaml` |
| `ImportError: cannot import name 'resolve_analysis_tree' from 'astra.helpers'` | Stale `astra-tools` (pre-0.2.5) | `pip install -U astra-tools` |
| `PermissionError` reading another user's symlinked `results/` | Cross-user scratch path without group ACLs | Request access from the data owner, or copy the manifests into your own scratch |
| `pip install` hangs or times out on a compute node | Compute nodes have no public internet | Always install from a login node |

---

## 7. Updating

=== "uv tool"
    ```bash
    uv tool upgrade lightcone-cli
    ```

=== "pip"
    ```bash
    pip install -U lightcone-cli astra-tools
    ```

=== "Source"
    ```bash
    cd ~/.lightcone/lightcone-cli
    git pull
    uv pip install -e .                       # only needed if pyproject.toml changed
    ```

    Editable installs auto-follow source edits — switching branches or pulling new commits is reflected immediately in `lc`. Re-install only when `pyproject.toml` adds a new dependency or changes the `[project.scripts]` table.

---

## 8. Uninstalling

=== "uv tool"
    ```bash
    uv tool uninstall lightcone-cli
    ```

=== "pip"
    ```bash
    pip uninstall lightcone-cli
    rm -rf ~/.lightcone/lightcone-cli         # only for source installs
    ```

!!! note "Keep your config?"
    `~/.lightcone/config.yaml` survives the uninstall. Delete it too if you want to start fresh.
