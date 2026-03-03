Template for the build spec section of CLAUDE.md.
Add this to the YAML frontmatter and body when starting a build loop.

---

Frontmatter field:

    build: open

Body section:

    ## Build Spec

    ### Desired State

    A working analysis that passes `/prism-verify`. All outputs materialized
    for the baseline universe. Scripts are parameterized by decisions.

    ### Evidence

    - `prism status` shows all outputs materialized
    - `astra validate astra.yaml` passes
    - Success criteria in `astra.yaml` are met
    - `/prism-verify` finds no failures

    ### Open Questions

    (Iterations add questions here when they encounter ambiguity the user should resolve.)
