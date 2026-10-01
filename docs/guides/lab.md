# Browse a project in Lightcone Lab

After this page you can open a Lightcone project in JupyterLab and read
its analysis, outputs, provenance and cited papers in one place.

!!! abstract "Planned page"
    What this page will cover:

    - Installing the extension with `pip install jupyterlab-lightcone` (JupyterLab 4.5.10 or later, below 5); it brings `lightcone-cli` with it.
    - Opening or creating a project from the Lightcone Lab launcher, and the status bar that names the current project.
    - The read-only inventory of `astra.yaml`: outputs, decisions, inputs, findings and a bibliography, with previews of figures, tables and metrics.
    - Output status markers for `behind` and `stale`, and the Provenance panel: last run, git revision, recorded recipe, input versions and environment.
    - Cited papers: where the cache is read from, **Fetch paper**, and jumping to a quoted passage.
    - Starting a Lightcone Agent chat in the project through Jupyter AI, and opening the report in the MySTRA viewer.

    Draws on: the `jupyterlab-lightcone` README
    ([PyPI](https://pypi.org/project/jupyterlab-lightcone/)), and
    [Outputs and provenance](../concepts/provenance.md) for what the
    status markers mean.
