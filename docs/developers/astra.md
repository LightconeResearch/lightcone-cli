# Built on ASTRA

Lightcone's analysis file, `astra.yaml`, follows ASTRA — the Agentic
Schema for Transparent Research Analysis — an open standard for
declaring a scientific analysis: its inputs, outputs, decisions, and
the evidence behind them. ASTRA is a specification, not a runner. It
says what an analysis is and stays out of execution; `lc` is the
execution layer built on top of it. Everything about what a spec
*means* — universe resolution, input references, conditional outputs,
the recipe placeholder grammar — is answered by ASTRA's reference
tooling rather than re-implemented in the engine (see
[Architecture](architecture.md)).

ASTRA is developed in the open, alongside Lightcone but separately from
it, with its own site, schema and tools. The schema is written in
LinkML. The `astra` CLI (the `astra-tools` package) validates specs,
generates and checks universes, and caches papers and verifies quotes.
A TypeScript SDK, `@astra-spec/sdk`, validates and resolves projects
for viewers and integrations; [Lightcone Lab](../guides/lab.md) reads
projects through it. Significant changes to the standard go through a
public RFC process.

You can work with ASTRA directly. If a spec resolves wrongly, the fix
belongs in astra-tools, not in `lc`. If the format cannot express what
your analysis needs, open an issue on astra-spec or propose an RFC.
ASTRA is in early alpha, so reports from real analyses are the most
useful input it can get.

- [astra-spec.org](https://astra-spec.org) — the specification site:
  concepts, elements, and a getting-started walkthrough.
- [RFC process](https://astra-spec.org/latest/rfc-process/) — how a
  proposal moves from idea to accepted; the RFCs themselves live in
  [`astra-spec/rfcs`](https://github.com/LightconeResearch/astra-spec/tree/main/rfcs).
- [LightconeResearch/astra-spec](https://github.com/LightconeResearch/astra-spec)
  — the LinkML schema and the documentation source.
- [LightconeResearch/astra-tools](https://github.com/LightconeResearch/astra-tools)
  — the `astra` CLI and Python SDK
  ([PyPI](https://pypi.org/project/astra-tools/)).
- [LightconeResearch/astra-typescript](https://github.com/LightconeResearch/astra-typescript)
  — the TypeScript SDK, published on npm as `@astra-spec/sdk`.
