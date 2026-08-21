# Engine Internals

The `lightcone.engine.*` modules, one page each: what the module owns,
its key symbols, and the invariants a change must keep. These are
hand-written tours, not generated API dumps — the engine is not a
public API (projects don't depend on lightcone-cli), so what matters
is responsibility and contract, not every signature.

## The map

| Module | Owns | Character |
|---|---|---|
| [`project`](project.md) | What a project is: convergence, discovery, mode, the `_run` seam | impure |
| [`dataset`](dataset.md) | How a project stores: git + git-annex, run records, restore | impure |
| [`identity`](identity.md) | `env_version`, `definition_version`, the lock scan | pure |
| [`plan`](plan.md) | The spec, read as a graph of tasks (through ASTRA) | pure |
| [`assets`](assets.md) | One output: its directory, manifest, and state | pure |
| [`worker`](worker.md) | Making one output; the rerun entry point | impure |
| [`materialize`](materialize.md) | The driver: gates, scheduling, the save/restore loop, status | impure |
| [`venue`](venue.md) | Where a run executes: SLURM detection, the login guard | impure |
| [`sandbox`](sandbox.md) | The exec boundary: policy, backends, attestation, denials | mixed |
| [`image` & `container`](container.md) | The container hatch: declaration → image → archive → runtime | pure / impure |
| [`crate`](crate.md) | The publication view: the repo as an RO-Crate | pure |

"Pure" here is a testing fact: pure modules are tested with nothing on
disk beyond `tmp_path` and nothing spawned; impure ones go through the
one subprocess seam (`project._run`) that the suite stubs — see
[Testing](../contributing/testing.md).

Two files sit outside the engine on purpose:

- **`lightcone/_sandbox_exec.py`** — the Landlock shim. Stdlib-only,
  zero lightcone imports; it runs on every sandboxed exec, and an
  engine import there would put click and the astra stack on that
  path. Pinned by tests.
- **`lightcone/cli/commands.py`** — the CLI: flags, rendering, exit
  codes. Imports the engine inside callbacks so `lc --help` stays
  cheap; never contains logic worth testing beyond rendering.
