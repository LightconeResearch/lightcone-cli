# lc run

Run an ad-hoc command in the project environment, under isolation.
This is the probe verb: it executes exactly one command the way a
recipe would be executed — same environment, same sandbox — so "does
it work under `lc run`?" and "will it work as a recipe?" are the same
question.

## Synopsis

```text
lc run CLUSTER_ID -- COMMAND...
```

The first argument is the cluster ID returned by `lc compute launch`.
Everything after `--` is the command, verbatim — flags included.
Argv, the `docker run` / `uv run` convention: a single quoted string
would be exec'd as one filename, so probe shell syntax through
`bash -c` instead. Set `CLUSTER` to your allocated cluster ID:

```bash
lc run "$CLUSTER" -- python -c "import numpy; print(numpy.__version__)"
lc run "$CLUSTER" -- python src/fit.py --points data/points.csv --outliers keep --output /tmp/probe
```

The command is submitted as an ordinary task to the cluster's Dask scheduler,
which chooses a worker. The command uses the prepared project environment and
the same sandbox as a recipe. stdout/stderr are forwarded as bytes, preserving binary output and
line endings when redirected. The client detaches on completion; the allocation
stays available until `lc compute down` or its time limit. A missing cluster ID
is an error, with no implicit local execution. See [compute](compute.md).

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
lc run "$CLUSTER" -- python -c "import scipy"        # is the package in the lock?
lc run "$CLUSTER" -- bash -c 'echo $HOME'            # see the private HOME a recipe gets
lc run "$CLUSTER" -- python src/fit.py --help        # exercise a script exactly as a recipe would
```
