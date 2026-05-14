# lightcone-cli

**lightcone-cli** is [Lightcone Research][lr]'s agentic execution layer for
[**ASTRA**][astra] (Agentic Schema for Transparent Research Analysis).  
It serves as the machinery that ties an analysis `astra.yaml` specification to a tree
of materialized outputs.

## Choose your path to the documentation

<div class="grid cards" markdown>

-   __I want to try it out__ – :lucide-rocket:

    ---

    Installation instructions, step-by-step tutorial, and fast tour of the lightcone framework and its agentic and workflow capabilities.

    [User Guide](user/index.md){ .md-button .md-button--primary }

-   __I want to contribute__ – :lucide-cog:

    ---

    In depth tour of the software architecture, agentic skills and API docs, as well as contribution instructions, aimed for
    contributors and maintainers.

    [Developer corner](maintainer.md){ .md-button .md-button--primary }

</div>

---

## Two libraries, one toolchain

<div class="grid cards" markdown>

-   __lightcone-cli__

    The library that ships the `lc` CLI which handle the agent surface (skills, plugins, guardrails) as well as the workflow execution layer. Depends on [**astra-tools**][astra-tools], the SDK for working with ASTRA analysis specifications.

    [:fontawesome-brands-github: Repository][cli]{ .md-button }

-   __astra-tools__

    The SDK for working with [**ASTRA**][astra] analysis specifications. This library provides the `astra` CLI which handles the [**ASTRA**][astra] lifecycle and validation process (schema, prior insights & findings, evidence verification helpers).

    [:fontawesome-brands-github: Repository][astra-tools]{ .md-button }

</div>

[lr]: https://lightconeresearch.org/
[astra]: https://astra-spec.org/latest/
[astra-tools]: https://github.com/LightconeResearch/astra-tools
[cli]: https://github.com/LightconeResearch/lightcone-cli
