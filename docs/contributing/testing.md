# Testing

The suite's shape follows the engine's: pure modules get pure tests,
the subprocess seam gets a stub, and the questions only a kernel, a
runtime, or a validator can answer get real ones — gated so they can't
pass by not running.

## The one seam

`tests/conftest.py`'s autouse `tools` fixture stubs
`engine.project._run` — the single choke point every external command
goes through — emulating each tool's observable effect (`uv lock`
writes `uv.lock`, `git init` makes `.git`, …) and recording every
argv. Under the stub the suite is hermetic: no network, no resolution,
no subprocesses.

The `real_tools` fixture opts back out, putting the real `_run` back.
Everything built on it (the `analysis` fixture, the rerun tests) does
spawn and may touch the network — that is the deliberate price of
testing execution.

## Where a question belongs

| Question | File | Character |
|---|---|---|
| Convergence semantics | `test_project.py` | stubbed |
| Template content & repair | `test_templates.py` | pure |
| Do bytes land in the annex? | `test_dataset.py` | **real tools** — every bug this seam had was invisible to a stub |
| Identity sensitivity | `test_identity.py` | pure, both directions |
| The graph, the gate | `test_plan.py` | pure — tests what lc *adds*, never what a spec means (that's astra-tools' suite) |
| Classification | `test_assets.py` | pure |
| One output, real recipe | `test_worker.py` | real boundary, real repo |
| The run, the record | `test_materialize.py` | real repos; one real `LocalCluster`; real `datalad rerun` |
| Venue detection & launch | `test_venue.py` | fakes the *host* (env vars, a stub srun), never the code |
| Policy / wrap / denial | `test_sandbox_*.py` | pure, run on every OS |
| The kernel's answer | `test_sandbox_enforcement.py` | gated |
| Image identity | `test_image.py` | pure — structure and ordering, never byte goldens |
| Runtime lifecycle | `test_container.py` | stubbed; refusals asserted on recorded argv |
| The runtime's answer | `test_container_smoke.py` | gated |
| The crate | `test_crate.py` | pure; the one byte claim is render-twice-identical |
| The validator's answer | `test_crate_smoke.py` | gated |
| CLI surface | `test_cli.py` | `CliRunner`; assert short unwrappable fragments |

## The enforcement suite

`test_sandbox_enforcement.py` is the only file that can tell you the
sandbox works, and four properties keep it honest:

1. **One suite, both mechanisms** — parameterized by `detect()` alone;
   a leak only Linux catches is a leak, and macOS CI is the sole place
   the generated SBPL ever executes.
2. **The real policy** — always `exec_policy`, never one hand-built to
   make the point. (`/usr` once sat in the exec set through a fully
   green suite built the other way.)
3. **Real leaks, tried literally** — undeclared tools executed,
   undeclared libraries `dlopen`ed, undeclared data read.
4. **It cannot pass by not running** — `LC_SANDBOX_TESTS_REQUIRED=1`
   in CI turns the skip into a failure, and two tests cover the guard
   itself.

**Mutation-check every denial test**: run the same command through
`Unavailable()` and confirm it *succeeds*. A denial test that would
pass unsandboxed is testing nothing, and the failure mode is silent.
Two related traps: a write-denial must target a path the OS would let
you write (a `/etc` write pins nothing), and enforcement fixtures must
not live under `/tmp`, which is inside the write baseline — the
`outside` fixture roots at `$HOME` for exactly this reason.

## Conventions

- Don't add a flag whose only user is a test — stub `project._run`
  instead.
- A forged-output test must break the annex hard link before writing
  (`test_materialize._forge` shows how) — results are committed thin,
  so an in-place write dirties every byte-identical sibling.
- Record formats are tested through their consumer (datalad's parser,
  the rocrate validator), never as golden files of our own JSON.
