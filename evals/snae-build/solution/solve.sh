#!/bin/bash
# Scripted reference solution (oracle): the build loop instruction.md asks
# for, done by hand. Runs as root in the seeded project; the verifier must
# score exactly 1.0 afterwards.
set -euo pipefail

PROJECT="${PROJECT:-/root/snae-build}"
SOLUTION="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT"

# 1-2. Complete the spec: formats, templated recipe commands, and each
#      output's inputs/decisions contract.
cp "$SOLUTION/astra.yaml" astra.yaml
astra validate astra.yaml

# 3. Write the scripts the recipes name, decisions parameterized via argparse.
mkdir -p scripts
cp "$SOLUTION"/scripts/*.py scripts/

# The project starts with no dependencies; declare what the recipes import.
uv add numpy scipy matplotlib

# 4. Commit, then build through the engine. Since lightcone-cli #226 the engine
#    runs recipes on an explicitly allocated cluster: launch the default local
#    one (all CPUs and RAM the container shows) and pass its name to every
#    materialize; --check and status need no cluster.
git add -A
git commit -q -m "Complete the spec and write the recipes"
lc compute launch --wait
lc materialize local

# Publication: declare a license, commit, materialize once more so the
# engine generates and commits ro-crate-metadata.json.
sed -i '/^\[project\]$/a license = "CC-BY-4.0"' pyproject.toml
git add -A
git commit -q -m "Declare a license for publication"
lc materialize local

# The gate instruction.md states, checked here so a broken oracle fails loudly.
astra validate astra.yaml
lc materialize --check
test -f ro-crate-metadata.json

# Release the allocation, as the instruction asks the agent to.
lc compute down local
