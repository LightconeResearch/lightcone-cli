# lc status

Report what state each of the analysis's outputs is in. Reads only: it
runs nothing, commits nothing, transfers no data, does not mind an
unclean tree, and always exits `0` — a state is not a failure. The
moment you most need to know where a project stands is when it isn't
clean, so this verb works there.

## Synopsis

```text
lc status [OPTIONS]
```

## Output

```text
  mode:    direct
  sandbox: landlock (fs: declared, network: allowed)

  · current  baseline/fit       a3f1f11
  · current  baseline/fit_plot  a3f1f11
  · behind   robust/fit         00cc14e  made under an earlier environment
  ! stale    robust/fit_plot    —        no manifest — it has never been materialized

2 current · 1 behind · 1 stale
```

The header is repository facts: which mode the project executes in
(and, for a containerized project, the image's tag and state), and
what enforcement a run on this host would get. No runtime and no
network is needed to answer either.

Then one line per output the spec declares, in dependency order: its
state, **the commit it was made at**, and — for anything not current —
why. The commit column is the verb's reason to exist: "which code made
this?" has an answer for a current output too, and for a `behind`
output that commit is where the environment that produced it can be
read back.

## States

- `current` — exactly what the spec asks for. Nothing to do.
- `behind` — still what the spec asks for; the environment moved since.
  Left alone by runs; `--refresh` remakes.
- `stale` — contradicts the project: definition changed, an input's
  content changed, or the output was edited by hand since it was made
  (a *foreign write* — the offending commit is named).

## Report vs gate

`lc status` reports; **`lc materialize --check` gates.** Two verbs
answering the same question with different exit codes is how a script
comes to depend on the wrong one, so the split is sharp: use status for
eyes, check for exit codes.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--json` | off | Emit the report as JSON on stdout. |

## The JSON report

```json
{
  "mode": "direct",
  "image": null,
  "sandbox": "landlock (fs: declared, network: allowed)",
  "counts": {"current": 4, "behind": 0, "stale": 0},
  "outputs": [
    {
      "output": "baseline/fit",
      "status": "current",
      "why": "",
      "git_sha": "a3f1f11791430d1becbe5548477b5910ab59a94a",
      "data_version": "sha256:939e9a55...",
      "foreign_write": ""
    }
  ],
  "warnings": []
}
```

Per output: the state, the reason (empty for `current`), the commit it
was materialized at and its content identity (both empty if it never
was), and `foreign_write` — the sha of a hand-edit's commit when one
was detected, which the prose `why` cannot carry for a machine
consumer. For a containerized project, `image` is
`{"tag": ..., "state": "present" | "absent" | "unfetched"}`.
