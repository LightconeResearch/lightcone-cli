# lightcone.engine.assets

One output: its directory, its manifest, and whether it is still
current. The classification rule lives here, next to the manifest it
reads and the hashes it compares — and it is the one place in the
engine where a bug is quiet rather than loud, which is why it may not
have two implementations.

Source: `src/lightcone/engine/assets.py`.

## Key symbols

| Symbol | Role |
|---|---|
| `classify(...)` | The one rule: `current` / `behind` / `stale`, with the why. Two callers — the worker and the read-only walk. |
| `Verdict.calls_for_a_remake(refresh=)` | The one place a state becomes an action: `stale` always, `behind` only when asked. |
| `data_version(path)` | Content hash of a directory or file — computed in the worker, before anything is annexed. |
| `Versions` | Per-run memo so a shared declared input hashes once, not once per dependent. |
| `read(sidecar)` / `write(...)` | The manifest, `.<output_id>.manifest.json`. Both take the sidecar's own path, so a caller holding an output path has to say `manifest_path` out loud. |
| `output_path(analysis_root, u, scope, id, fmt)` | The output's file, guarded: any part that is not a single path component is refused, and so is a format that could not be an extension. |
| `manifest_path(output)` | The sidecar beside it, named from the id alone — so it keeps its path, and its history, across a re-declared format. |
| `ContentNotFetchedError` | An annexed file whose content is not in this clone, in either shape it takes. |

## What must stay true

- **One `classify`, two callers, one differing value.** The worker
  hands live input digests; check mode hands `None` for anything
  upstream that will run ("this is going to change"). That value is
  the entire difference — never a second body of logic. History (the
  foreign-write fact) enters the same way: computed by whoever has
  git, handed in as a value.
- **The comparison is fourfold**: `definition_version`, the declared
  input *set* (separate on purpose — a dropped dependency moves
  neither hash), each recorded input digest, then `env_version`.
  `stale` wins over `behind`; `behind` does not propagate and a behind
  upstream still feeds its dependents.
- **A skip returns the *recorded* digest, never a recomputed one** —
  on a bytes-free clone, rehashing dangling symlinks would quietly
  report a different output.
- **Unfetched content refuses loudly, in both shapes.** A pointer file
  hashes to a well-formed digest of the wrong thing; a dangling
  symlink drops out of an `is_file()` walk without a word. Both raise
  `ContentNotFetchedError` naming `git annex get`; only dangling
  symlinks are added back to the directory walk.
- **`calls_for_a_remake` has three callers** (worker, check, the
  cascade walk) and no inline re-spellings — the third copy is where
  they start to disagree.

## Tests

`tests/test_assets.py` — pure; nothing on disk beyond `tmp_path`.
The pointer-file and dangling-symlink traps are pinned against real
annex shapes in `tests/test_dataset.py`.
