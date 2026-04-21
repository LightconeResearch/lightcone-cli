# lightcone-cli — justfile
# Install just: https://github.com/casey/just
# Usage: just <recipe>

# Show available recipes
default:
    @just --list

# ── Development ────────────────────────────────────────────────────────────────

# Sync all dependency groups (dev + docs)
install:
    uv sync --all-groups

# Sync only the dev group
install-dev:
    uv sync --group dev

# Run the test suite
test *ARGS:
    uv run pytest {{ ARGS }}

# Run tests with coverage report
test-cov:
    uv run pytest --cov=src/lightcone --cov-report=term-missing --cov-report=html

# Lint with ruff + mypy
lint:
    uv run ruff check src/ tests/
    uv run mypy src/

# Auto-fix lint issues
fix:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

# Format code
fmt:
    uv run ruff format src/ tests/

# Run all checks (lint + tests)
check: lint test

# ── Documentation ──────────────────────────────────────────────────────────────

# Sync the docs dependency group
docs-install:
    uv sync --group docs

# Build the documentation site (outputs to site/)
docs: docs-install
    uv run zensical build

# Build with strict mode (fail on warnings; --strict is accepted but not yet enforced by zensical)
docs-strict: docs-install
    uv run zensical build --strict

# Serve documentation with live reload at http://127.0.0.1:8000
docs-serve: docs-install
    uv run zensical serve

# Serve on a custom port
docs-serve-port port="8080": docs-install
    uv run zensical serve --dev-addr 0.0.0.0:{{ port }}

# Remove the built site directory
docs-clean:
    rm -rf site/

# ── Package ────────────────────────────────────────────────────────────────────

# Build the wheel and sdist
build:
    uv build

# Show the current version (from git tag via hatch-vcs)
version:
    uv run hatch version

# Clean build artifacts
clean:
    rm -rf dist/ build/ site/ *.egg-info src/*.egg-info

# ── Evals ──────────────────────────────────────────────────────────────────────

# Sync the eval optional dependency
evals-install:
    uv sync --extra eval

# Run all skill evals
evals: evals-install
    uv run lc eval run

# Run evals for a specific skill
evals-skill skill: evals-install
    uv run lc eval run --skill {{ skill }}
