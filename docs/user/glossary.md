# Glossary

**ASTRA** — the specification language (`astra.yaml`): inputs, outputs,
recipes, decisions, universes. lightcone-cli is its execution layer.
ASTRA carries analysis structure only; the environment lives in the uv
project files.

**direct mode** — the default execution mode: the locked environment
lives in the project tree (`.venv`), recipes run on the host inside the
OS sandbox, no image exists.

**containerized mode** — entered by declaring `[tool.lightcone.image]`
(or a `Containerfile.extra`): the generated, content-addressed image
becomes the execution world for engine, workers, recipes, and probes.

**env_version** — the environment identity: a hash of `uv.lock`,
`.python-version`, the uv install settings, and the declared system
layer. Part of every output's `code_version`; an environment edit
stales every materialized output, visibly.

**code_version** — the identity of one output's materialization
semantics: recipe text, active decisions, `env_version`, and the
output's sandbox escalation. `lc status` compares it against manifests.

**data_version** — the content hash of an output directory. `lc verify`
recomputes it to detect tampering.

**manifest** — `.lightcone-manifest.json`, written beside every
materialized output: the versions above plus input hashes, git state,
runtime attestation, image identity, and the hermeticity record. The
provenance chain is manifests referencing manifests.

**hermeticity** — the manifest field recording what enforcement a
recipe *actually* ran under: mechanism (`landlock`, `seatbelt`,
`podman+landlock`, `none`), file scope (`declared`, `project-rw`,
`open`), and network posture (`denied`, `allowed`, `unenforced`).

**sandbox** — the OS enforcement (Landlock on Linux, Seatbelt on macOS)
that restricts each recipe to its declared set: own output dir
writable, project + declared inputs readable, locked environment plus a
versioned utility allowlist executable.

**system layer** — apt packages (and optionally a digest-pinned base
image) declared in `[tool.lightcone.image]`; what flips a project into
containerized mode.

**image tag (`lc-env-<hash>`)** — the content-addressed identity of the
generated image: a pure function of the rendered Containerfile,
`pyproject.toml`, and `uv.lock`. Code edits never move it.

**probe (`lc run`)** — an arbitrary command run in byte-for-byte the
recipe environment (lock, interpreter, sandbox included), with writes
confined to the tmp scope. Probes never materialize outputs.

**universe** — one assignment of options to the analysis's declared
decisions (`universes/<id>.yaml`). Outputs materialize per universe
under `results/<universe>/<output>/`.

**decision** — a declared methodological choice with enumerated
options; the multiverse is the set of defensible option combinations.

**blast radius** — the count of materialized outputs an environment
edit stales, printed at decision time: "environment changed: N
materialized output(s) are now stale".

**pre-migration** — a manifest written by an earlier schema version.
Shown distinctly by `lc status`; `lc verify` still checks the hashes it
carries.
