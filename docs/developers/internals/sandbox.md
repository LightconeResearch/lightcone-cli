# lightcone.engine.sandbox

The exec boundary: what a command may touch, and how that is enforced.
A `Policy` says *what* in mechanism-free path sets; a `Backend` turns
it into **a different argv that sandboxes itself**; `boundary` picks
one, runs it, and reports what was actually enforced. `run.py` (the
`lc run` engine) and the worker are the two consumers.

Source: `src/lightcone/engine/sandbox/` — `model.py`, `policy.py`,
`boundary.py`, `landlock.py`, `seatbelt.py`, `oci.py`, `denial.py` —
plus `lightcone/_sandbox_exec.py`, the Landlock shim.

## Key symbols

| Symbol | Role |
|---|---|
| `Policy` | What we will enforce: path sets, env overlay, exec allowlist. No mechanism ever appears in it. |
| `Capability` | What this host can do — `detect()`'s answer, the only `sys.platform` branch. |
| `Attestation` | What was actually enforced, derived from the flags applied — never from what the matrix says should have happened. |
| `Backend.wrap(policy, argv)` | The pure rewrite. `contains_prefix` declares whether the uv hop rides inside (a container is a world; a host mechanism trusts host plumbing). |
| `exec_policy(...)` | Shared policy builder with caller-supplied write scope and `use_gpus` setting. Building it creates a private `$HOME`; `scope()` owns its cleanup. |
| `Unavailable` | A real backend that wraps to the same argv and attests `fs: open`. Saying so is the caller's job; pretending is nobody's. |
| `denial.explain()` / `denial.trailer()` | Best-guess remedies (allowed to return nothing) and the unconditional trailer on every nonzero sandboxed exit. |

An optional output receiver gets stdout/stderr byte chunks. Capturing output never
decodes or normalizes stdout; only the retained stderr tail is decoded for denial
classification. Without a receiver, stdout remains inherited.

`exec_policy(..., use_gpus=True)` passes the allocation's `CUDA_VISIBLE_DEVICES`
and optional `CUDA_DEVICE_ORDER` through unchanged. CPU commands receive an empty
CUDA mask. Direct GPU policies grant existing NVIDIA character device nodes.
Native permissions still apply; visibility is cooperative, and the reusable
worker's environment is never modified.

The pure OCI rewrite adds podman-hpc's `--gpu` for GPU commands. Runtime selection
refuses explicit GPU recipes with ordinary Docker or Podman; probes on those
runtimes receive a CPU policy and an explanatory note. CPU containers remain
supported on all runtimes and set `NVIDIA_VISIBLE_DEVICES=void` to override image defaults.
See [GPU allocations](../../guides/cluster.md#gpu-allocations).

Container recipes and probes explicitly receive the worker's effective
`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and `OPENBLAS_NUM_THREADS` values when set.
This preserves Dask Nanny's numerical-library thread settings across the OCI
boundary. Container environment variables remain an explicit allowlist.

## What must stay true

- **`wrap` stays pure** — no temp files, no FDs, no global state
  (pinned by `test_wrap_is_pure`). That is what makes every backend
  testable on a host that cannot run it, and it is why the Landlock
  policy travels as JSON on argv rather than an inherited ruleset FD.
- **The shim stays alone**: stdlib only, zero lightcone imports, setup
  failures exit the reserved 97, and it never falls through to running
  the command unsandboxed.
- **Never grant EXECUTE on a directory that could be a system
  prefix.** Landlock unions rights over ancestors, so one EXECUTE on
  `/usr` outranks the whole per-file allowlist — with every test still
  green, because the allowlisted binaries are exactly the ones that
  were going to work. This shipped once (a venv on a system python);
  the rule and its test are the fix.
- **SBPL is last-match-wins; Landlock unions.** The asymmetry decides
  where a rule can live: the macOS guard takes back writes the
  vendored defaults hand out, and the write tier is restated *after*
  the guard — get the order wrong and layer 4 materializes on Linux
  and refuses on macOS with the golden test still green.
- **Anything every backend must do belongs to the seam** — the env
  overlay is composed in `boundary.env_argv()` once, for every
  mechanism, so a mechanism added later cannot forget what it never
  had to remember. (While each backend applied its own, `Unavailable`
  applied none.)
- **The macOS profiles are vendored, not authored** (codex-derived,
  provenance header, single delta) — the read baseline is a list of
  things that break, found one production failure at a time. Put our
  rules in the generator, keep `diff` against upstream as the re-sync
  tool.
- **A denial is never invisible**: `explain()` may find nothing, so
  the trailer fires on every nonzero exit, unconditionally. Remedies
  name only what exists today.

## Tests

The suite splits along the seam:
`test_sandbox_policy/wrap/denial.py` (pure, every OS),
`test_sandbox_shim.py` (the shim as a real subprocess),
`test_sandbox_oci.py` (the mount table, pure), and
`test_sandbox_enforcement.py` — **the kernel's answer**, one suite for
both mechanisms, run against the *real* `exec_policy`, with
`LC_SANDBOX_TESTS_REQUIRED=1` turning "no mechanism, skip" into a hard
failure in CI. Every denial test is mutation-checked through
`Unavailable()` — a denial test that would pass unsandboxed is testing
nothing, silently.
