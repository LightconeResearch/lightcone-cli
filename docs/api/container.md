# lightcone.engine.image & container

The container hatch, split down the pure/impure line. `image.py` is
what a containerized project *declares* and how that becomes an
identity — pure, no subprocess anywhere. `container.py` is building,
storing and entering images — impure, every command through
`project._run`. The exec side (the mount table) lives with the other
backends in `sandbox/oci.py`.

Sources: `src/lightcone/engine/image.py`,
`src/lightcone/engine/container.py`, `src/lightcone/engine/sandbox/oci.py`.

## Key symbols

| Symbol | Role |
|---|---|
| `image.declaration(root)` | The `[tool.lightcone.image]` table, validated — a closed key set (`base`, `apt-install`, `run-commands`, `env`), because every key is hashed. |
| `image.tag(root)` | `lc-env-<16 hex>` over the rendered Containerfile *and* the identity document. |
| `image.archive_path(root, tag)` | `.datalad/environments/<tag>/image` — the `datalad containers-add` layout. |
| `container.build(root)` | Build + save + commit, idempotent; returns `(Runtime, "built" \| "present")`. |
| `container.runtime_for_run(root, *, build)` | One function, two strictnesses: `lc build`/materialize-preflight may build and commit; the probe and worker only ever find, fetch, and load. |
| `container.backend(...)` | The single construction point for the exec backend — the only mode branch. |
| `container.sync(...)` | The in-container environment converge: network on, project `:rw`, host uv cache mounted, into `.lightcone/venv`. |
| `Runtime` | Facts only — root/mode/name/tag/id/arch — never mechanism. |

## What must stay true

- **The user never sees a Containerfile.** The render exists only in a
  transient build context; the image's `LABEL` carries the identity
  document so the archive stays self-describing. There is deliberately
  no `pip-install` key — the Python environment is the lock's
  business, never the image's.
- **The engine never enters the image.** The container is the
  *recipe's* world: driver, git, annex, and classification stay the
  host's `lc`; exactly two things run in-image — the sync and each
  exec. Network is uncontrolled on every mechanism, symmetrically, and
  the attestation says so — no consumer may read a promise into
  "containerized".
- **No project file enters the build context** — that is what makes
  "code edits never rebuild" structural rather than incidental.
- **The dataset is the store; runtime stores are caches.** Execution
  pins the archive's config-blob **id** (readable with no runtime),
  never a tag; a dropped archive never substitutes — a rebuild is a
  new archive under a new id.
- **Builds and archive commits happen only on a clean tree, and only
  after the graph resolves** — a refusal over a typo must not cost a
  minutes-long build, and `dataset.save` commits the whole index.
- **The mount table is the mechanism** (`sandbox/oci.py`): project
  `:ro`, `results/` `:rw`, declared inputs `:ro`, private HOME,
  `--tmpfs /tmp`, over a `--read-only` rootfs — without that flag a
  stray write *succeeds* into the ephemeral layer and vanishes while
  the attestation claims `fs: declared`. Mounts are resolved source,
  **declared** destination — the one policy shape that keeps its paths
  unresolved, because they are addresses the recipe uses.
- **Runtime differences are spellings, never shapes.** One
  `OCIBackend`, data-parameterized; the podman family is stated once
  (`_PODMAN_FAMILY`) and asked positively, so a new runtime falls
  outside it by default. podman-hpc adds exactly one step (`migrate`,
  outside the load branch) and joins `_SHARED_STORE_RUNTIMES`.
  Detection order podman-hpc → podman → docker; docker's daemon is
  probed at detection.
- **Site container modules are named, not silenced.** `site_modules()`
  reports the `ENABLE_*` gates set for podman-hpc, which the runtime
  applies from its own environment: a module can widen the container
  past the mount table (`ENABLE_CVMFS` binds `/cvmfs`,
  `ENABLE_MPICH_SS` adds `--privileged` and the host namespaces), so
  they reach `Attestation.site_modules` and every manifest rather than
  leaving `fs: declared` to overstate the boundary. `MOUNT_*` never
  appears there — `project.child_env` scrubs it before the runtime
  sees it.
- **A stale squashed image is healed before the load.** The tag is
  deterministic but builds are not bit-reproducible, so a rebuild
  migrated under an unchanged tag would put a second same-named image
  into podman-hpc's read-only squash store — after which every storage
  operation fails. `_heal_squash` probes the store and `rmsqi`s each
  stale copy **by id** first (before the load, which a wedged store
  also fails) — `rmsqi <tag>` resolves only one record and could take
  the current image instead. A store too wedged to list is removed by
  tag and re-probed, bounded by `_HEAL_ATTEMPTS`; a probe outcome the
  heal does not recognize is left for migrate's own loud path, so it
  can never break a healthy run.
- **The architecture gate refuses before the load** — a wrong-arch
  `load` succeeds and then dies as `exec format error` deep inside a
  recipe. Ignorance passes; a recorded mismatch refuses, naming the
  fix.

## Tests

`tests/test_image.py` (pure: structure and ordering, tag sensitivity
both ways, the `env_version` frame), `tests/test_container.py`
(lifecycle against the stubbed `_run` — every refusal on recorded
argv), `tests/test_sandbox_oci.py` (the mount table, pure), and
`tests/test_container_smoke.py` — the runtime's answer, gated by
`LC_CONTAINER_TESTS_REQUIRED=1` in CI, building a real image and
proving the record on a bytes-free clone with a real `datalad rerun`.
