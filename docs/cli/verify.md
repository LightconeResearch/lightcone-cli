# lc verify

```text
lc verify [-u UNIVERSE]
```

Audit the provenance chain by recomputing hashes — like `lc status`,
offline and Snakemake-free. Exit 1 when any check fails.

## Failure modes

| Failure | Meaning |
|---|---|
| `tampered_data` | the bytes on disk no longer hash to the recorded `data_version` — the output was edited after materialization |
| `missing_manifest` | an output directory exists with no manifest — it was produced by something other than `lc materialize` |
| `broken_chain` | a recorded upstream `data_version` no longer matches the upstream's current one — the upstream was re-run without rebuilding this output |

## Notes

Orthogonal to pass/fail, verify surfaces provenance facts the hashes
can't express, per output:

| Note | Meaning |
|---|---|
| `unsandboxed` | no enforcement mechanism ran when this output was produced |
| `dirty_tree` | materialized from an uncommitted working tree — the recorded `git_sha` can't fully reproduce it |
| `pre_migration` | an earlier-schema manifest; the hashes it carries are still checked |

CI can gate on enforcement with
`lc materialize --require-sandbox=declared-fs`.
