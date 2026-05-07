# Installing and Using lightcone-cli on NERSC

A practical guide for running [`lightcone-cli`](https://github.com/LightconeResearch/lightcone-cli) on Perlmutter. The CLI works the same as anywhere else, but the filesystem layout, container runtime, and SLURM submission have NERSC-specific quirks that are worth knowing about up front.

---

## 0. Install Claude Code

`lightcone-cli` is the execution layer of the `lightcone` project — it harnesses a coding agent (e.g. Claude Code) to follow the `astra` standard while building and running an analysis. So the very first step, even before touching `lightcone-cli` itself, is to install the agent. For now the project is built around **Claude Code**, which can be installed via:

```bash
curl -fsSL claude.ai/install.sh | bash      # installs to ~/.local/bin/claude
```

Add `~/.local/bin` to your `PATH` if it isn't already, then verify and authenticate:

```bash
claude --version
claude                                      # first run prompts for login (claude.ai or API key)
```

Other install routes (npm, native package managers) are documented in the [Claude Code installation docs](https://docs.claude.com/en/docs/claude-code/setup).

---

## 1. Pick a Python environment

Next, set up a Python environment for `lightcone-cli` (Python 3.11+ required). There are two practical options on Perlmutter:

### Option A — conda env (recommended)

```bash
module load conda                           # NERSC's miniconda
conda create -n your-env-name python=3.11 -y
conda activate your-env-name
```

Conda envs land under `~/.conda/envs/` (your home, not CFS). They're persistent across sessions; just `conda activate your-env-name` next time. 

> The home disk quota on NERSC is capped at 40 GB, so for larger envs it's worth moving the env to `$SCRATCH` and pointing the original location at it via a symlink:
>
> ```bash
> # Move the env once it's created, then symlink the original location
> conda deactivate
> mv ~/.conda/envs/your-env-name $SCRATCH/conda-envs/
> ln -s $SCRATCH/conda-envs/your-env-name ~/.conda/envs/your-env-name
> ```
>
> Caveats: `$SCRATCH` is purged on a 12-week rolling window — the env will silently disappear. If you go this route, set up a periodic `touch` job or use `/global/cfs/cdirs/<project>/conda-envs/` instead.
>
> See [NERSC's Python guide](https://docs.nersc.gov/development/languages/python/nersc-python/) for the full storage strategy and [the `ln(1)` man page](https://man7.org/linux/man-pages/man1/ln.1.html) for the symlink syntax.

### Option B — venv inside an existing conda env

If you already have a project conda env (e.g. `lightcone`) and just want `lc` available alongside it without polluting the conda env:

```bash
module load conda
conda activate lightcone
python -m venv ~/.lightcone/.venv          # or wherever you prefer
source ~/.lightcone/.venv/bin/activate
```

**Pitfall:** if `lc` ends up installed in more than one env (e.g. both the conda env and a venv), the wrong one can shadow the other on `PATH`. After install, always run `which lc` to confirm you're getting the binary you expect.

---

## 2. Install lightcone-cli

With the environment ready, install the package itself.

### From PyPI (recommended)

`lightcone-cli` and its companion package `astra-tools` are both published to PyPI, so a single command does it:

```bash
pip install lightcone-cli astra-tools
```

### From source

You're also welcome to install from source — useful if you want to follow the latest commits or contribute back to the repo. Note the GitHub repo for `astra-tools` is named `ASTRA`:

```bash
cd ~/.lightcone                              # or wherever you keep clones

git clone https://github.com/LightconeResearch/lightcone-cli.git
pip install -e ./lightcone-cli               # editable install, follows local edits

git clone https://github.com/LightconeResearch/ASTRA.git
pip install -e ./ASTRA                       # same for astra-tools
```

For development work, add the dev extras:

```bash
pip install -e "./lightcone-cli[dev]"        # adds pytest, ruff, mypy
```

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

The agent (Claude Code) will invoke `lc run` for you when it decides recipes need to materialize — you don't call it directly. What you control is *where Claude Code is running*: it inherits whatever shell environment you started it from. To get the agent's `lc run` calls onto a compute node, start `claude` from inside a Slurm allocation:

```bash
salloc -A <your_project> -q interactive -C gpu --nodes=1 -t 00:30:00
# allocation drops you onto a compute node; from there:
cd /path/to/your-analysis
claude
```

Now anything the agent decides to run (`lc run`, scripts, etc.) executes on the allocated node, not the login node.

The `interactive` QoS on the GPU partition is appropriate for development. For longer or larger sessions, other QoS queues will be supported in the future.

> Unattended batch submission (`sbatch`-style runs of `lc`) is not yet supported — for now, every analysis runs interactively under an allocation that's open while you work.




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
