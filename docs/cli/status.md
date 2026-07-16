# lc status

Manifest-driven status report for every output declared in `astra.yaml`,
annotated with recorded asynchronous SLURM jobs.

## Synopsis

```text
lc status [OPTIONS]
```

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--universe`, `-u NAME` | every universe in `universes/*.yaml` | Restrict to one universe. |
| `--json` | off | Emit machine-readable JSON instead of a styled table. |

## Output

Per universe, one line per declared output:

```
Universe baseline
  ✓ ok    accuracy
  ✸ stale precision
  ✗ miss  recall
  → alias inference
  ◷ queued simulation (job 1234567, regular)
  ▶ running fit (job 1234568, shared)
```

Statuses (defined in `lightcone.engine.status.StatusLiteral`):

| Status | Meaning | When you see it |
|--------|---------|-----------------|
| `ok` | Manifest present, recomputed `code_version` matches what the manifest recorded. | The output is up to date. |
| `stale` | Manifest present, but `code_version` drifted. | You changed the recipe, image, or a decision since the last run. `lc run` will re-execute. |
| `missing` | No manifest at the expected output path. | Never built, or the directory was deleted. |
| `alias` | The output has no `recipe:` of its own — it's just a name pointing at a sibling output (typical for ASTRA "promoted" outputs from sub-analyses). | Status is implicitly determined by the upstream. |

When `.lightcone/jobs/*.json` records exist, `lc status` batch-queries
`squeue` and `sacct`. Affected outputs can additionally show `queued`,
`running`, `failed`, `cancelled`, or `unknown`, with the job id, QoS, and
failure log where relevant. Completed jobs fall back to the manifest status,
because manifests—not scheduler history—remain the source of truth for
outputs. JSON output includes a nullable `job` object beside each output's
materialization `status`.

## Why it doesn't import Snakemake

`lc status` reads per-output manifests and small async job records. It never
imports Snakemake or touches `.snakemake/`; scheduler polling happens only
when records exist. If Slurm commands are unavailable, cached states are
preserved. That makes it usable on:

- A fresh clone before any `lc run`.
- A frozen archive copied off a cluster.
- A read-only workspace.

If a manifest is missing, the output reports `missing`. If a manifest is
unparseable, `read_manifest` returns `None` and you also see `missing`
— that is the agent-forged-file scenario; investigate with `lc verify`.

## Examples

```bash
lc status                       # every output, every universe
lc status --universe baseline   # just baseline
lc status --json                # machine-readable JSON output
```

## Related

- [`lc verify`](verify.md) — recomputes data hashes too (slower; catches
  tampering and broken chains).
- [`lc cancel`](cancel.md) — cancel a queued or running recorded job.
- [api/status](../api/status.md) — the Python API.
- [api/manifest](../api/manifest.md) — the manifest schema.
