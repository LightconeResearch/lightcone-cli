# lc status

```text
lc status [-u UNIVERSE] [--json]
```

Report materialization status for every declared output — **offline and
local-only** by invariant: it reads manifests, `pyproject.toml`, and
the local image record; never the network, never Snakemake state. A
fresh clone of a finished project reports its state with no setup.

## The header

Three lines answer the questions nothing else surfaces:

```text
mode:    containerized (3 system packages)        # or: direct
image:   lc-env-9f2c81d44a1b03e7 — built (sha256:…)   # or: needs build
sandbox: landlock (fs: declared, network: unenforced) # this host
```

## Per-output states

| State | Meaning | What to do |
|---|---|---|
| `ok` | manifest matches the current `code_version` | nothing |
| `stale` | recipe, decisions, or **environment** drifted since materialization | `lc materialize` re-runs it |
| `missing` | no manifest — never materialized (or produced outside `lc`) | `lc materialize` |
| `pre-v2` | manifest from an earlier schema — not comparable to the current identity | re-materialize when convenient |
| `alias` | a `from:` re-export; no independent state | — |

After the listing, an environment edit's blast radius is stated
explicitly: `environment changed: N materialized output(s) are now
stale`.

`--json` emits the same information machine-readably (used by CI and
agents).
