# CLI reference

Bare `lc <verb>` is the canonical invocation on every machine — no
activation, no `python -m`, no per-venue spelling. The launcher
discovers the project (nearest `astra.yaml` walking up), detects the
mode, converges the environment, and delegates execution verbs to the
project's own locked engine.

| Verb | Runs in | Does |
|---|---|---|
| [`lc init`](init.md) | tool env | idempotently converge the project scaffold |
| [`lc materialize`](materialize.md) | project engine | execute recipes, write manifests |
| [`lc run`](run.md) | project engine | probe: arbitrary commands in the recipe environment |
| [`lc status`](status.md) | tool env | offline, manifest-driven status report |
| [`lc verify`](verify.md) | tool env | recompute hashes, audit the provenance chain |
| [`lc build`](build.md) | tool env | build the environment image (containerized mode) |
| [`lc export`](export.md) | tool env | publishable RO-Crate bundles |

"Tool env" verbs work before a lock exists (or offline on a frozen
archive); execution verbs run from the engine version pinned inside the
project's own `uv.lock` — in containerized mode, from inside the
project image.
