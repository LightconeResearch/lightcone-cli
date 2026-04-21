# lc target

Show or manage execution targets for this project.

## Synopsis

```
lc target [OPTIONS]
lc target add [NAME]
lc target edit NAME
```

## Description

`lc target` manages which execution target a project uses. It reads and writes `.lightcone/lightcone.yaml`.

Without subcommands or flags, shows the current project target.

## Options

| Option | Description |
|--------|-------------|
| `--set NAME` | Set the project target to NAME |
| `--list` | List all available targets |
| `--show NAME` | Show a target's configuration |

## Subcommands

### `lc target add [NAME]`

Interactive wizard to create a new execution target. Prompts for site type, connection details, container runtime, node type, QOS, and resource limits. Saves to `~/.lightcone/targets/{name}.yaml`.

### `lc target edit NAME`

Edit an existing target's configuration interactively. Press Enter to keep the current value of any field.

## Examples

```bash
lc target                          # show current project target
lc target --list                   # list all targets
lc target --set perlmutter-gpu     # change project target
lc target --show perlmutter-gpu    # inspect a target's config
lc target add my-cluster           # create a new target
lc target edit perlmutter-gpu      # edit an existing target
```

## Target configuration format

Targets are stored as YAML files in `~/.lightcone/targets/`:

```yaml
site: perlmutter
backend: slurm
connection:
  hostname: perlmutter.nersc.gov
  username: myusername
account: m4031_g
container_runtime: podman-hpc
constraint: gpu
qos: regular
max_nodes: 4
max_walltime_minutes: 360
max_concurrent_jobs: 8
```

| Field | Description |
|-------|-------------|
| `backend` | `slurm`, `docker`, `local` |
| `connection.hostname` | SSH hostname (SLURM only) |
| `connection.username` | SSH username (SLURM only) |
| `account` | SLURM allocation account |
| `container_runtime` | `docker`, `podman`, `podman-hpc` |
| `constraint` | SLURM `--constraint` value |
| `qos` | SLURM `--qos` value |
| `max_nodes` | Resource limit cap for Claude |
| `max_walltime_minutes` | Resource limit cap for Claude |
| `max_concurrent_jobs` | Resource limit cap for Claude |
