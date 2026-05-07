#!/usr/bin/env bash
# Build script for docs.lightconeresearch.org, run by Cloudflare Workers
# (Static Assets). Installs uv, syncs the docs group, runs zensical → site/.
set -euo pipefail

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv sync --group docs
uv run zensical build
