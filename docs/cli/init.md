# prism init

Create a new ASTRA analysis project with full agentic scaffolding.

## Synopsis

```
prism init [OPTIONS] [DIRECTORY]
```

## Description

`prism init` bootstraps a new ASTRA analysis project. It creates:

- Directory structure (`universes/`, `scripts/`, `results/`, `.prism/`)
- Boilerplate `astra.yaml` with TODO placeholders
- `Containerfile` and `requirements.txt`
- A baseline universe (`universes/baseline.yaml`)
- `.claude/` directory with skills, hooks, scripts, and `settings.json`
- `.prism/prism.yaml` linking the project to its execution target
- `.prism/dagster.yaml` pointing Dagster's SQLite store to `results/.dagster/`
- `CLAUDE.md` from the plugin template
- Python virtual environment (`.venv/`) with `lightcone-prism` installed
- Git repository with an initial commit

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `DIRECTORY` | `.` | Project directory to create |
| `--no-git` | false | Skip git initialisation |
| `--no-venv` | false | Skip virtual environment creation |
| `--target`, `-t` | user default | Execution target name to write into `.prism/prism.yaml` |
| `--permissions` | saved/prompt | Claude Code permission tier (`yolo`, `recommended`, `minimal`) |
| `--existing-project` | — | Path to existing code to migrate into the new project |
| `--sub-analysis` | false | Create a sub-analysis directory and wire it into the parent project |

## Modes

### New project

```bash
prism init my-analysis
prism init my-analysis --target perlmutter-gpu
```

Creates a fresh project in `my-analysis/`.

### Migrate existing code

```bash
prism init . --existing-project .
prism init my-analysis --existing-project ../old-code
```

If `source != directory`, copies the source contents first. Adds Prism infrastructure without overwriting existing files. Then run `/prism-migrate` inside Claude Code to generate `astra.yaml`.

### Sub-analysis

```bash
prism init analyses/new_stage --sub-analysis
prism init --sub-analysis new_stage   # placed under analyses/
```

Scaffolds a sub-directory with its own `astra.yaml` and baseline universe, and wires it into the parent project's `astra.yaml` and universe files.

## Permission tiers

Chosen once at setup and saved to `~/.prism/config.yaml`:

| Tier | Behaviour |
|------|-----------|
| `yolo` | All tools allowed, including MCP. No guardrails. |
| `recommended` | Full access; denies `sudo`, `git push`, SSH/AWS dotfiles, HPC scratch. |
| `minimal` | Read-only; every write/shell action needs explicit confirmation. |

## Post-init

```bash
cd my-analysis
claude           # open Claude Code
# → run /prism-new to scope your research question
```

## Internal helpers

The following private functions do the heavy lifting and are tested directly:

- `_create_dagster_yaml(directory)` — writes `.prism/dagster.yaml`
- `_create_boilerplate_astra_yaml(directory)` — writes `astra.yaml`, `Containerfile`, `requirements.txt`, `universes/baseline.yaml`
- `_create_claude_settings(directory, tier, target)` — copies plugin files and writes `.claude/settings.json` + `.claude/settings.local.json`
- `_create_prism_config(directory, target_name)` — writes `.prism/prism.yaml`
- `_init_sub_analysis(directory)` — scaffolds sub-analysis directory and wires it into the parent spec
