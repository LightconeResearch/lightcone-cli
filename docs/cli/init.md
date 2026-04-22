# lc init

Create a new ASTRA analysis project with full agentic scaffolding.

## Synopsis

```
lc init [OPTIONS] [DIRECTORY]
```

## Description

`lc init` bootstraps a new ASTRA analysis project. It creates:

- Directory structure (`universes/`, `scripts/`, `results/`, `.lightcone/`)
- Boilerplate `astra.yaml` with TODO placeholders
- `Containerfile` and `requirements.txt`
- A baseline universe (`universes/baseline.yaml`)
- `.<harness>/` directory with skills, agents, guides (and hooks, scripts, `settings.json` for Claude Code)
- `.lightcone/lightcone.yaml` linking the project to its execution target
- `.lightcone/dagster.yaml` pointing Dagster's SQLite store to `results/.dagster/`
- `CLAUDE.md` from the plugin template
- Python virtual environment (`.venv/`) with `lightcone-cli` installed
- Git repository with an initial commit

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `DIRECTORY` | `.` | Project directory to create |
| `--no-git` | false | Skip git initialisation |
| `--no-venv` | false | Skip virtual environment creation |
| `--target`, `-t` | user default | Execution target name to write into `.lightcone/lightcone.yaml` |
| `--permissions` | saved/prompt | Claude Code permission tier (`yolo`, `recommended`, `minimal`) |
| `--tools` | `claude` | Agent harness(es) to install (repeat for multiple: `--tools claude --tools codex`) |
| `--existing-project` | — | Path to existing code to migrate into the new project |
| `--sub-analysis` | false | Create a sub-analysis directory and wire it into the parent project |

## Modes

### New project

```bash
lc init my-analysis
lc init my-analysis --target perlmutter-gpu
lc init my-analysis --tools claude --tools codex   # install both Claude Code and Codex harnesses
```

Creates a fresh project in `my-analysis/`. By default only the Claude Code harness (`.claude/`) is installed. Pass `--tools` once per harness to install additional ones.

### Migrate existing code

```bash
lc init . --existing-project .
lc init my-analysis --existing-project ../old-code
```

If `source != directory`, copies the source contents first. Adds lightcone-cli infrastructure without overwriting existing files. Then run `/lc-migrate` inside Claude Code to generate `astra.yaml`.

### Sub-analysis

```bash
lc init analyses/new_stage --sub-analysis
lc init --sub-analysis new_stage   # placed under analyses/
```

Scaffolds a sub-directory with its own `astra.yaml` and baseline universe, and wires it into the parent project's `astra.yaml` and universe files.

## Permission tiers

Chosen once at setup and saved to `~/.lightcone/config.yaml`:

| Tier | Behaviour |
|------|-----------|
| `yolo` | All tools allowed, including MCP. No guardrails. |
| `recommended` | Full access; denies `sudo`, `git push`, SSH/AWS dotfiles, HPC scratch. |
| `minimal` | Read-only; every write/shell action needs explicit confirmation. |

## Post-init

```bash
cd my-analysis
claude           # open Claude Code
# → run /lc-new to scope your research question
```

## Internal helpers

The following private functions do the heavy lifting and are tested directly:

- `_create_dagster_yaml(directory)` — writes `.lightcone/dagster.yaml`
- `_create_boilerplate_astra_yaml(directory)` — writes `astra.yaml`, `Containerfile`, `requirements.txt`, `universes/baseline.yaml`
- `_install_harnesses(directory, tier, target, tools)` — copies skills/agents/guides to every selected harness; installs hooks, scripts, and `settings.json` for Claude Code only
- `_create_lightcone_config(directory, target_name)` — writes `.lightcone/lightcone.yaml`
- `_init_sub_analysis(directory)` — scaffolds sub-analysis directory and wires it into the parent spec
