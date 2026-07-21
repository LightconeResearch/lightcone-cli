# lc init

Scaffold a new ASTRA project with Claude Code integration.

## Synopsis

```text
lc init [OPTIONS] [DIRECTORY]
```

`DIRECTORY` defaults to `.` (the current directory).

## What it creates

Inside `DIRECTORY` (creating it if needed):

```text
astra.yaml                    # tiny boilerplate spec with one example output
CLAUDE.md                     # short note pointing future agents at the project
.gitignore                    # Python + lightcone state
.lightcone/
  lightcone.yaml              # currently a stub: { target: local }
results/                      # placeholder; populated by `lc run`
universes/                    # placeholder; populate via `astra universe generate -n …`
.claude/                      # bundled Claude Code plugin
  skills/, agents/, hooks/, scripts/, templates/
  settings.json               # the chosen permission tier
.venv/                        # Python venv (skipped with --no-venv)
```

`lc init` refuses to run if `DIRECTORY/astra.yaml` already exists.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip `python -m venv .venv`. |
| `--github [NAME\|OWNER/NAME\|URL]` | prompt (TTY only) | Connect the project to this GitHub repository without prompting; created if it doesn't exist. |
| `--private` / `--public` | prompt (public on a hub, private elsewhere) | Visibility when `--github` creates a new repository. |
| `--no-github` | off | Skip the GitHub connection step. |
| `--permissions {yolo,recommended,minimal}` | `recommended` | Which `.claude/settings.json` permission tier to install. |

> The historical `--target`, `--existing-project`, and `--sub-analysis`
> flags have been removed. For migrating an existing project, run
> `lc init` in a fresh directory and use the `/lc-from-code` skill from
> inside Claude Code.

## The GitHub step

A repository backs the analysis up, makes it shareable, and on a
lightcone JupyterHub deployment it is what the image builder clones for
cloud runs — so `lc init` actively offers to connect one. On a TTY it
asks; scripted runs use the flags (no flags → a one-line hint, nothing
blocks). The flow:

1. **Auth**: an ambient `GH_TOKEN`/`GITHUB_TOKEN` or an authenticated
   `gh` CLI is used silently. Otherwise, where a device-flow client id
   is configured (`LIGHTCONE_GITHUB_CLIENT_ID` — injected on the hub),
   `lc` runs GitHub's device authorization natively: enter a one-time
   code at github.com/login/device, done. The token is handed to `gh`
   (`gh auth login --with-token` + `gh auth setup-git`) so the single
   authorization also powers `git push` and everything an agent might
   do with `gh`; it lives in your home directory, which on the hub is
   the NFS volume — it survives server restarts.
2. **Repository**: one free-form prompt accepting a bare `name` (under
   your account), `owner/name`, or the URL of an existing repo. If it
   exists it is connected; if not it is created — private supported
   (note: the hub's image builder can only clone *public* repos until
   the deployment configures a clone token, so on the hub the
   visibility prompt defaults to public).
3. **Push**: the scaffold is committed (a repo-local git identity is
   derived from your GitHub login if none is configured) and pushed
   with `origin` set upstream.

The step is never fatal: any failure or Ctrl-C leaves a fully
scaffolded local project and prints how to connect later
(`gh repo create --source . --push`).

## Permission tiers

| Tier | Allowed | Denied |
|------|---------|--------|
| `yolo` | `Bash(*)`, `Edit`, `Read`, `Write`, `WebSearch`, `WebFetch`, `mcp__*` | — |
| `recommended` | `Read`, `Edit`, `Write`, `Bash(*)`, `WebSearch`, `WebFetch` | Edits to `~/.ssh`, `~/.aws`, `~/.gnupg`, `/scratch`, `/pscratch`; `sudo`, `rm -rf`, `git push`. |
| `minimal` | `Read` | Everything else. |

The tiers are defined as `PERMISSION_TIERS` in
`src/lightcone/cli/commands.py` — adjust there if you want to add a tier
or change defaults.

## Examples

```bash
lc init                                # scaffold in cwd, recommended tier
lc init my-analysis                    # scaffold in ./my-analysis
lc init my-analysis --no-git --no-venv # bare bones
lc init . --permissions yolo           # for autonomous loops you trust
```

## Next steps

```bash
cd my-analysis
claude           # open Claude Code
# Inside Claude Code:
/lc-new  # scope a research question into astra.yaml
# Then ask the agent to implement the spec.
# It will run lc run, watch lc status, then validate and verify.
```
