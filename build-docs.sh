#!/usr/bin/env bash
# Cloudflare Pages build script for docs.lightconeresearch.org.
# Installs uv, syncs the docs group, and runs `zensical build` → site/.
set -euo pipefail

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv sync --group docs
uv run zensical build
