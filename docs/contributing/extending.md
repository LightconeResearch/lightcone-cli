# Extending the Codebase

Where each kind of change belongs, what to read first, and the
invariant it must keep. The engine has one implementation per rule —
most review feedback is some form of "that spelling already exists;
use it".

## The map

| To change… | Edit | Keep true |
|---|---|---|
| What a scaffolded file contains | `engine/templates/files/*.tmpl` (+ `test_templates.py`) | A template gets a function only when a value must be decided or a merge policy held. |
| What gets converged | `engine/project.py` (+ `test_project.py`) | Everything through `_Converger.item`/`.file`/`.blocked`; repairs only append; only what git can carry. |
| How a project stores bytes | `engine/dataset.py` + `gitattributes.tmpl` (+ `test_dataset.py`, real annex) | Every command through `project._run`; nobody is asked to run git-annex. |
| How an output is identified | `engine/identity.py` (+ `test_identity.py`) | Sensitivity both ways: what must move the hash, what must not. Length-framing stays. |
| When an output is remade | `engine/assets.py` (+ `test_assets.py`) | One `classify`; callers differ by one input value, never by logic. Ask first: does the change *contradict* the project (stale) or is it *circumstance* (behind)? |
| How the spec becomes a graph | `engine/plan.py` (+ `test_plan.py`) | Ask `astra.resolve`; a missing answer is a PR to astra-tools; ambiguity is a `ProjectError`, never a guess. |
| How a recipe runs | `engine/worker.py` (+ `test_worker.py`) | Never raises; no git; mutation-check every denial test. |
| What a run commits | `engine/materialize.py` (+ `test_materialize.py`) | The driver owns git alone; the tree ends as clean as it started. |
| Where a run executes | `engine/venue.py` + `cluster_for_run` (+ `test_venue.py`) | One detection ladder; venues detected, never configured; test by faking the host. |
| Supporting a new HPC center | `venue._SITES` | One row — marker + the center's own `salloc`/`sbatch` spellings, verified against its documentation, never guessed. |
| What a sandboxed command may touch | `sandbox/policy.py` (+ `test_sandbox_policy.py`) | Path sets only — no mechanism leaks in. |
| Adding a sandbox mechanism | one module in `sandbox/` + one line in `detect()` | `wrap` pure, `attest` honest, `contains_prefix` answered. Nothing above the seam changes. |
| A denial message | `sandbox/denial.py` (+ `test_sandbox_denial.py`) | Remedies copy-pasteable and real *today*; the trailer stays unconditional. |
| What the image is made of | `engine/image.py` (+ `test_image.py`) | Pure; every declaration key hashed; structure tests, never byte goldens. |
| How images are built/stored/entered | `engine/container.py` + `sandbox/oci.py` (+ `test_container.py`) | `runtime_for_run`'s two strictnesses; runtime differences are spellings inside `OCIBackend`, never new shapes. |
| What the crate says | `engine/crate.py` (+ `test_crate.py`) | Pure builder: sorted, no clock, git injected; render-twice-identical. The validator floor lives in `test_crate_smoke._FLOOR`. |
| How a foreign write is detected | `dataset.last_writer` + `materialize._foreign_write` | History, never hashing; `datalad_run_subject` is the one spelling of the record's subject. |
| A CLI verb | `cli/commands.py` (+ `test_cli.py`) | Logic in the engine; raise `ProjectError`; render here; engine imports stay inside callbacks. |

## Rules that apply everywhere

- **Land code, tests, and dependencies together.** A dependency enters
  `pyproject.toml` with the change that needs it, never speculatively.
- **No dead code, no foreshadowing.** Nothing references a verb, flag,
  or feature that doesn't exist yet; `lc --help` advertises only what
  works.
- **No escape hatches.** Enforcement ships without a flag to turn it
  off; there is deliberately no `--no-sandbox`, no `--force`, no
  rebuild-the-world flag.
- **Nothing waits on a human.** No prompt, no interactive shell —
  either is a hang for the agents that run these verbs most.
- **Refusals carry remedies, and remedies are verified.** A message
  that tells someone to run a command has been run; a center's
  spellings come from its documentation.
- **Docstrings are Google-style, comments carry *why*.** A design
  decision gets a sentence; its history belongs in the design record,
  not the code.

## Conventions

Ruff (E, F, I, N, W, UP; line length 100), mypy strict with
`namespace_packages = true`. `src/lightcone/` must never gain an
`__init__.py` — the namespace is shared with future sibling
distributions, and a real package there breaks the contract.
