# lc run

Run an ad-hoc command in the project environment, under isolation.
This is the probe verb: it executes exactly one command the way a
recipe would be executed — same environment, same sandbox — so "does
it work under `lc run`?" and "will it work as a recipe?" are the same
question.

## Synopsis

```text
lc run COMMAND...
```

Everything after `run` is the command, verbatim — flags included.
`lc run` takes no options of its own, so nothing needs escaping:

```bash
lc run python -c "import numpy; print(numpy.__version__)"
lc run python src/fit.py --points data/points.csv --outliers keep --output /tmp/probe
```

## What it does

- **Converges the environment first.** The probe syncs `.venv` to the
  lock before executing, so what you probe is what a recipe gets.
- **Applies the recipe policy.** The project tree is read-only apart
  from `results/`, declared inputs are readable, undeclared tools
  don't execute. On a containerized project, the command runs inside
  the committed image (which must already be built — the probe never
  builds).
- **Proxies the exit code.** `lc run` exits with the command's own
  code — `128 + N` when a signal killed it — so scripts and pipelines
  read it exactly as they would the bare command.
- **Explains denials.** On a nonzero exit, a note on stderr says the
  command ran sandboxed; when the failure looks like a denial, the
  note names the path and the remedy (`uv add` for a missing package,
  an ASTRA input declaration for data, `results/` or
  `tempfile.mkdtemp()` for writes).

A probe has no output and writes no manifest: nothing it does is
recorded anywhere. Any uv project works — `lc run` doesn't require an
`astra.yaml`, only `pyproject.toml`, `uv.lock` and `.venv` in the
current directory.

## What it is not

There is no sandbox opt-out and no flag surface — a command that needs
more than the policy grants is a command that would fail as a recipe,
and the fix (declare the dependency) is the same in both places.

## Examples

```bash
lc run python -c "import scipy"        # is the package in the lock?
lc run bash -c 'echo $HOME'            # see the private HOME a recipe gets
lc run python src/fit.py --help        # exercise a script exactly as a recipe would
```
