# lightcone-cli

**lightcone-cli** is Lightcone Research's agentic execution layer for
**ASTRA** (Agentic Schema for Transparent Research Analysis). It ships
the `lc` executable, a small set of Claude Code skills, and the
provenance/integrity machinery that ties an `astra.yaml` spec to a tree
of materialized outputs.

This documentation has two halves.

<div class="grid cards" markdown>

-   __I want to try this out__

    ---

    Start with a short installation guide followed by a step-by-step tutorial. Continue with instructions to continue on a computer cluster. Learn about the agentic framework a short, step-by-step, with
    worked examples. You will not need to read any Python.

    [:lucide-rocket: User Guide](user/index.md){ .md-button .md-button--primary }

-   __I want to contribute to lightcone-cli__

    ---

    Welcome — keep reading. The rest of this page is a fast tour for
    contributors and maintainers; deep dives live in the sub-trees of the
    nav.

    [:lucide-cog: Developer corner](maintainer.md){ .md-button .md-button--primary }

</div>

---

## Two packages, one toolchain

**lightcone-cli** depends on [**astra-tools**][astra-tools], the SDK for working with ASTRA analysis specifications.

[**astra-tools**][astra-tools] provides the `astra` CLI which handles the
whole ASTRA lifecycle and validation process (schema, validation, prior insights & findings, evidence verification helpers).

**lightcone-cli** provides the `lc` CLI which handle the agent surface (skills, plugins, guardrails) as well as the workflow execution layer.

[:lucide-book-open: Read more on the ASTRA specification](https://astra-spec.org/latest/){ .md-button }

## Where to read next

- [Architecture](architecture.md) — the full execution and integrity story
- [CLI Reference](cli/index.md) — every command currently shipped
- [Python API](api/index.md) — the engine modules
- [Skills](skills/index.md) — what each `/lc-*` skill does (including the `/lc-from-*` family)
- [Contributing](contributing/setup.md) — getting the dev loop running

[astra-tools]: https://github.com/LightconeResearch/astra-tools