# CLAUDE.md

## Project: snae

ASTRA (Agentic Schema for Transparent Research Analysis) analysis project, built with lightcone-cli.

### Skill Commands

| Command | Purpose |
|---------|---------|
| `/lc-new` | Scope question, structure decisions, integrate literature |
| `/lc-migrate` | Migrate an existing project into ASTRA / lightcone-cli framework |
| `/lc-build [description]` | Build loop -- spec to materialized results. Optional description guides plan priorities |
| `/lc-verify` | Verify results and decision-code alignment |
| `/lc-feedback` | File a bug report from the current session |

**Workflow:** `/lc-new` or `/lc-migrate` --> `/lc-build` --> `/lc-verify`

### Source of Truth

- `astra.yaml` -- The analysis specification. Read this first.
- `universes/` -- Decision selections (one YAML per universe).

### Project Layout

```
astra.yaml              # Specification: decisions, inputs, outputs
lightcone.yaml            # lightcone-cli config (default target, etc.)
CLAUDE.md             # This file
Containerfile         # Container image for execution
requirements.txt      # Python deps (keep in sync with scripts)
universes/
  baseline.yaml       # Default decision selections
scripts/              # Implementation scripts
results/<universe>/   # Outputs by universe (produced by lc run)
```

### Development Workflow

Three overlapping phases:

1. **Write & Debug** -- Run scripts directly (`python scripts/compute.py`) to iterate. Write them recipe-ready from the start: parameterize decisions, write to convention paths, one script per output.
2. **Integrate** -- Add `recipe:` blocks to outputs in `astra.yaml`. Track with `lc status` (`no recipe` / `pending` / `ok`). Container build specs (Containerfile or image string) can be set at the analysis level or per-recipe.
3. **Materialize** -- `lc run` executes via Dagster in containers (Docker or SLURM). Falls back to local execution if Docker is unavailable. Done when `lc status` shows all `ok`.

**An output is not done until `lc run` produces it.** Running scripts directly is for debugging only — final results must always come from `lc run` so they are reproducible inside containers.

### Spec-Code Invariant

**`astra.yaml` must always reflect the code and vice versa.** When you change one, update the other immediately:
- Add a decision to code? Add it to `astra.yaml` and all universe files.
- Add an output or change a script? Update the `recipe:` block in `astra.yaml`.
- Remove or rename something? Update both sides and run `astra validate astra.yaml`.

### Decision Parameterization

**Every decision must be parameterized in code** -- never hardcode a decision value. Accept all decisions as CLI args.

**Underscore convention:** IDs use underscores in `astra.yaml` (`prior_range`). lightcone-cli passes `--prior_range wide`. Scripts must match: `parser.add_argument('--prior_range')`, **not** `--prior-range`.

### Writing Results

Convention path: `results/<universe_id>/<output_id>.<ext>` -- no `path` field needed.

- `metric` -- JSON (`{"value": 0.95}`)
- `figure` -- PNG
- `table` -- CSV
- `data` -- Parquet/HDF5
- `report` -- Markdown

### CLI Reference

```bash
# astra -- spec operations
astra validate astra.yaml                       # Validate (run after every change)
astra info [--decisions]                      # Analysis summary / decision details
astra universe generate -n NAME [-d "desc"]   # Generate universe from defaults
astra universe check universes/x.yaml         # Check universe constraints

# lc -- execution operations
lc run [OUTPUT] [--universe NAME]        # Execute recipes via Dagster (auto-builds)
lc status [--universe NAME]              # Materialization + container status
```

### Status Interpretation

`lc status` shows outputs vs universes. **Progression:** `no recipe` --> `pending` --> `ok`

- `ok` -- Recipe exists, results on disk. Done.
- `pending` -- Recipe exists, not materialized. Run `lc run`.
- `no recipe` -- No `recipe:` block yet. Still in Write & Debug phase.

---

## Analysis Context

### Domain Context

- This analysis is a **building block** for a larger cosmological analysis — downstream work will consume the best-fit parameters.
- Union2.1 data file has 5 tab-delimited columns: SN name, redshift, distance modulus, statistical error, and a fifth column (systematic or probability-related). Header contains SALT2 light-curve fit parameters (alpha, beta, delta) and absolute magnitude M for h=0.7 with and without systematics.
- Flat LCDM assumes Omega_M + Omega_L = 1, so the model is fully specified by H0 and Omega_L.
- Luminosity distance integral in flat LCDM requires numerical integration (no closed-form); `scipy.integrate.quad` or similar is standard.

### Implementation Notes

- **Data**: download or bundle the Union2.1 text file. Parse with numpy/pandas, skip comment lines.
- **Libraries**: `scipy.optimize.minimize` for MAP fitting, `scipy.integrate.quad` for cosmological integrals, `matplotlib` for plots.
- **Bounds**: H0 in [50, 100] km/s/Mpc, Omega_L in [0, 1].
- **Distance modulus**: mu = 5 * log10(d_L / 10pc), where d_L is luminosity distance in flat LCDM.
