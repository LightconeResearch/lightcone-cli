# Cite papers and verify evidence

After this page you can back a decision with a quote from a paper, and
have every quote in the spec checked against the paper's PDF.

!!! abstract "Planned page"
    What this page will cover:

    - `prior_insights` and `findings`, and the `evidence` entries behind them: a DOI with a verbatim quote, or an artifact naming an output.
    - Caching a paper with `astra paper add <doi>` (`--version N` for an arXiv revision), and finding the PDF with `astra paper path <doi>`.
    - Writing a quote a machine can find: `quote.exact`, plus `prefix` and `suffix` when the text occurs more than once.
    - Checking one paper's quotes with `astra paper verify-quotes <doi>`, and the whole project with `astra validate --verify-evidence`.
    - What a failed quote means, and why artifact-backed evidence is reported as skipped rather than failed.
    - Asking the agent for a literature pass while scoping.

    Draws on: [Evidence](../concepts/evidence.md), the citations section
    of the `astra` skill (`agent-skills/plugins/lightcone/skills/astra/SKILL.md`),
    the `lightcone` plugin's `references/literature.md`, and the
    `astra paper` command group (`astra-tools/src/astra/cli.py`).
