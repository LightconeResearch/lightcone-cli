"""Lightcone execution engine.

Snakemake-backed orchestrator for materializing astra.yaml outputs.
Provenance is recorded in per-output content-addressed manifests
(``.lightcone-manifest.json``) co-located with each output.
"""
