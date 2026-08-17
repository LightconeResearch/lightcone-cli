# User guide

lightcone-cli (`lc`) turns an `astra.yaml` analysis specification into
a tree of materialized, provenance-tracked outputs — with the
environment locked, the execution sandboxed, and every result carrying
a manifest that says exactly how it was made.

- [Install](install.md) — uv is the only prerequisite.
- [Getting Started](getting-started.md) — from empty directory to
  verified outputs.
- [The Environment](environment.md) — the locked environment, the
  sandbox, and the container hatch.
- [Troubleshooting](troubleshooting.md) — the errors you may meet and
  what they're telling you.
- [Glossary](glossary.md) — the vocabulary in one place.

## The workflow at a glance

!!! example "A complete session"

    ```bash
    lc init my-analysis && cd my-analysis
    uv add numpy astropy          # dependencies: always uv
    $EDITOR astra.yaml            # describe the analysis
    lc run python src/explore.py  # probe in the recipe environment
    lc materialize                # produce outputs + manifests
    lc status                     # what exists, what's stale
    lc verify                     # audit the provenance chain
    lc export wrroc -o out.zip --zip   # publishable bundle
    ```

Four verbs: **`lc run` probes, `lc materialize` executes, `lc status`
reports, `lc verify` audits.**
