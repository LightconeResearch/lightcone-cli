# VERIFY — generate the gate, then close it

VERIFY is the heart of the reproduction. Every prior phase builds the pipeline; VERIFY decides whether the pipeline reproduces the *paper*. We cannot pre-write a gate for a specific paper's claims — the claims **are** the paper. So VERIFY generates the gate: for every replication target in `targets/targets.md` it **writes a test** that encodes the paper's claim, then runs the suite, then where a test fails it **diagnoses, fixes the implementation, re-runs, and re-tests** — looping until the suite is green or the fidelity intent says "reasonable-ish, stop."

This is TDD applied to a paper. The claims are the spec; the tests are the gate; green is the goal. The workflow drives it in two movements — a `parallel` test-writing fan-out, then a bounded fix-loop — and this file is the contract for both the test-writer agents and the fix agents.

## Inputs

- `targets/targets.md` — the replication-target ledger: per target, priority + expected value + **stated uncertainty** + comparison guidance. This is what each test is written against.
- `astra.yaml` — output definitions; each target maps to an output, so each test knows where to read its result.
- `results/baseline/<output>/` — the reproduced result the test loads and asserts on.
- `work/reference/figures/` + `work/reference/tables/` — the paper's own figures and tables (the comparison reference for structural figure tests and table-cell tests). The target's row in `targets/targets.md` names which figure/table each target maps to; `targets/targets.md` itself is the ledger, not where the figures live.
- `work/reference/source/` (arXiv `.tex`) or `work/reference/document.md` (PDF fallback) — the paper text. Grep for "what does the paper actually claim for this number / what does it say Figure 3 should show" so the test encodes the *real* claim, not a paraphrase of the target row.
- `work/reference/code/` + `code-index.md` (when present) — **canonical** for numerics/method. The fix step reads the module `code-index.md` maps to the failing output to diagnose divergence: "what does the reference code compute here that ours misses."
- `PLAN.md` "Fidelity intent" + `args.intent` — the **stopping criterion**. It sizes the fix-loop (see *Bounded by intent*).

## Part 1 — Write a test per target

One worker per target, in `parallel` — `tests/test_<target>.py` files are disjoint, so they write without conflict. Each worker returns a `TEST_SCHEMA` object (`target_id`, `test_path`, `kind ∈ {metric, table, figure}`, `claim` verbatim where possible, `tolerance` = the bar it asserts). It does **not** edit `astra.yaml`.

Each test reads its reproduced result from `results/baseline/<output>/` and the expected value + uncertainty from the target's row in `targets/targets.md` (grep the paper source to confirm the claim). The bar each kind asserts:

**Metric test** — assert the reproduced value lies **within the paper's stated uncertainty** of the expected value. The tolerance is the paper's reported precision (the ±, the confidence interval, the significant figures it quotes) — *not* an arbitrary `rtol`, and not bare floating-point equality. If the paper says `S₈ = 0.776 ± 0.017`, the test passes the reproduced value when it falls in `[0.759, 0.793]`. Where the paper quotes only digits, the last quoted digit sets the bar. Scientific equivalence is the standard; exact match is neither expected nor required.

**Table test** — assert the **key cells** named in the target's comparison guidance first, each to its own stated precision, then the remaining cells. A table reproduces when its scientific content matches cell-by-cell at the paper's quoted precision; one off cell among the headline numbers fails the test, a rounding wobble in a tertiary cell does not.

**Figure test** — assert **structural features, never pixels**. Stochastic methods and rendering differences produce pixel variation that says nothing about fidelity. The features that *do* carry the claim: overall shape / trend, axis ranges, key features (peaks, inflections, zero-crossings), curve **ordering** (which series sits above which), and **magnitudes** (the right order of magnitude in the right place). Read the paper's caption and the reference figure in `work/reference/figures/` (the target's row in `targets/targets.md` names which) for what the figure is *supposed* to show, and encode those features as assertions on the reproduced data (load the underlying array where the output emits one; fall back to extracting features from the rendered figure only when no array is available). The test passes when the same scientific conclusion follows from the reproduced figure as from the paper's.

Commit each test as it lands. Tests are **durable, committed artifacts** — the gate this reproduction generated, re-runnable by any later session.

## Part 2 — The fix-loop

Run the suite; where it fails, fix the *implementation* and re-run; iterate until green or intent-bounded. Each round:

1. **Run.** `lc run --universe baseline` re-materializes any stale output, then `pytest tests/`. A status-only agent returns a `VERDICT_SCHEMA` object — per target `{pass, reproduced, expected, diagnosis}`, plus `all_pass` and the list of `failing` target_ids. This step **only reports**; it fixes nothing.
2. **Stop?** If `all_pass`, done. If the round budget (from intent) is spent, accept what's close and stop — log the still-failing targets to `open-questions.md`.
3. **Fix.** One fix worker per failing target (`parallel` where the failures live in disjoint outputs). Each reads its test, the verdict's diagnosis, the script that produces the output, and the **canonical reference code**. It makes the **smallest correct change to the implementation** — the script, the recipe, or a decision's baseline value — so the claim holds, then commits.
4. **Re-test.** Loop back to step 1 over the **full** suite (see *Mind interdependence*).

A useful diagnosis names the root cause at `path:line`: not "the result is wrong" but "`scripts/bao_fit.py:42` uses `damping_prior=flat`; the paper specifies Gaussian (§4.2) — change to `gaussian`." The fix lands there.

## Two disciplines

**(a) Bounded by intent.** The fix-loop's depth is **not** a fixed number — it comes from the interviewed fidelity intent, passed in `args.intent` and recorded in `PLAN.md`. The workflow derives a max-round budget from it ("afternoon / sanity" → 1–2 rounds, accept what's close; "overnight / headline" → 3–4; "no deadline" → push every target to green). VERIFY reads the intent, sizes its own loop, and weights its effort by target priority within that budget — *this is why ORIENT interviews for fidelity intent.* A reproduction asked for "an afternoon" that burns a day of fix rounds has ignored its governing parameter; one asked for "no deadline" that stops at the first partial has under-delivered.

**(b) Mind interdependence.** Outputs depend on each other — a covariance feeds a χ², a calibration feeds three downstream metrics. A fix for one target can regress a sibling that was passing. So after **every** fix round, re-run the **full** suite, not just the target you touched. The committed test suite is the regression net; running all of it each round is what makes the net hold.

## Hard rules

- **NEVER weaken a test to make it pass.** The test encodes the paper's claim; loosening the tolerance, deleting an assertion, or asserting on a different quantity erases the gate. If a target won't go green, the fix is in the *implementation* — or the gap is genuinely the paper's.
- **A genuine paper gap goes to `open-questions.md`, not into the test.** When the paper is under-specified (a tolerance it never states, a value it only plots, a method step it omits) and the intent says stop here, log the gap — which target, what the paper does and doesn't pin down, where the reproduction landed — and move on. That is an honest open question for the human at close-out, not a test failure to engineer away.
- **Code is canonical where it disagrees.** When `work/reference/code/` exists and the paper and code disagree on a numeric or method, the fix follows the code (record the disagreement in `CLAUDE.md`'s disagreements log + `open-questions.md`). Without code, the paper's prose is the only anchor; converge against it and surface residual gaps honestly.
- **Real data only.** A test never passes by feeding the script synthetic stand-in input. If the test can't read a real result, that's a RUN failure to fix, not a fixture to fake.
- **Empty `failing` is a real signal.** When the suite goes green, say "every target reproduces within the paper's stated bars" — don't pad the verdict with hedges the data doesn't support.

## What VERIFY returns

The fix-loop's final `VERDICT_SCHEMA` is the phase's product: per-target pass/fail with reproduced-vs-expected and a one-line diagnosis for each residual failure, `all_pass`, and the `failing` list. REVIEW reads it to synthesize the side-by-side `report.html`; the human reads it (and the `open-questions.md` entries it generated) at close-out. The tests themselves persist in `tests/` as the durable, re-runnable gate.
