# lc run

Run an ad-hoc command in the project environment, under isolation.
This is the probe verb: it executes exactly one command the way a
recipe would be executed — same environment, same sandbox. Recipes must also
declare the resources they need, including their GPU count.

## Synopsis

```text
lc run CLUSTER -- COMMAND...
```

The first argument is a cluster name or full immutable ID from `lc compute launch`.
Everything after `--` is the command, verbatim — flags included.
Argv, the `docker run` / `uv run` convention: a single quoted string
would be exec'd as one filename, so probe shell syntax through
`bash -c` instead. Set `CLUSTER` to your allocated cluster's name or full ID:

```bash
lc run "$CLUSTER" -- python -c "import numpy; print(numpy.__version__)"
lc run "$CLUSTER" -- python src/fit.py --points data/points.csv --outliers keep --output /tmp/probe
```

The command is submitted as an ordinary task to the cluster's Dask scheduler,
which chooses a worker. The command uses the prepared project environment and
the same sandbox as a recipe. stdout/stderr are forwarded as bytes, preserving binary output and
line endings when redirected. The client detaches on completion; the allocation
stays available until `lc compute down` or its time limit. A missing cluster,
or one that is not yet active with every expected worker connected, is an
error: nothing waits and nothing runs locally instead. Use
`lc compute status CLUSTER --wait` first. See [compute](compute.md).

The command receives EOF on stdin; terminal input and pipes into `lc run` are not
forwarded. Pass input files through the project's declared inputs instead.
For direct execution, ambient environment variables come from the worker's
allocation environment. Prefixing the CLI with `NAME=value` does not forward
that variable to an existing cluster. Set command-specific values inside the
command, for example `lc run "$CLUSTER" -- env NAME=value python script.py`.
Containerized commands use the image's environment and the sandbox overlays.

The command reserves one worker's full CPU, memory, and GPU budgets for its duration.
Its CUDA mask exposes only the reserved devices; a CPU-only allocation exposes
none, even on a host with GPUs. A recipe instead declares its GPU count explicitly.
See [GPU allocations](../user/cluster.md#gpu-allocations) for container prerequisites.

Interrupting the CLI detaches its client; the remote command may still be running.
Stop the allocation with `lc compute down` and its full ID (a name can already
belong to a newer allocation) before working with files the interrupted command
could still be writing. Confirm that the command has
stopped; local containers may require separate termination through their runtime
(see [execution limits](../user/cluster.md#execution-requirements-and-limits)).

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
