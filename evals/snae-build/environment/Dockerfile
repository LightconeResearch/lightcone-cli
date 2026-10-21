FROM python:3.12-slim

# lc scrubs ambient UV_* settings from recipe environments, except these two:
# a shared managed interpreter and a shared package cache, seen identically
# by root (oracle, verifier) and the unprivileged agent user.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_CACHE_DIR=/opt/uv-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        jq \
        procps \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv==0.12.13 \
    && uv python install 3.12

# The engine under test is a wheel the eval workflow builds from its checkout
# into lc-wheel/ (gitignored), installed the way a user installs a release —
# `uv tool install`, which also links the git-annex executables — but into
# system-wide paths so every user sees the same tools. astra-tools is the
# version that wheel depends on.
COPY lc-wheel/ /opt/lc-wheel/
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin \
    uv tool install --python 3.12 /opt/lc-wheel/lightcone_cli-*.whl \
    && UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin \
    uv tool install --python 3.12 \
        "astra-tools==$(/opt/uv-tools/lightcone-cli/bin/python -c 'import importlib.metadata as m; print(m.version("astra-tools"))')"

# A git identity for whoever commits, and root reading the agent-owned repo
# in the verifier without git's dubious-ownership refusal.
RUN git config --system user.name "Lightcone Eval" \
    && git config --system user.email eval@lightcone \
    && git config --system init.defaultBranch main \
    && git config --system --add safe.directory '*'

# `lc init`, the seed overlaid, the universe regenerated from the seed spec's
# defaults, then a clean seed commit (lc materialize refuses a dirty tree).
# No scientific dependencies are pre-seeded — declaring them (`uv add`) is
# part of what the task measures.
RUN lc init /root/snae-build
COPY seed/ /root/snae-build/
RUN cd /root/snae-build \
    && rm -f universes/baseline.yaml \
    && astra universe generate -n baseline \
        -d 'Default configuration using standard practices' \
    && git add -A \
    && git commit -q -m 'Seed the eval task'

# Warm the shared cache with the packages the recipes will plausibly need so
# a trial does not hinge on PyPI; the project itself stays dependency-free.
RUN uv init /tmp/warm \
    && cd /tmp/warm && uv add numpy scipy matplotlib \
    && rm -rf /tmp/warm

# The harness runs the agent as an unprivileged user and the verifier as
# root: tools and interpreter readable by all, cache and project writable,
# and /root traversable (it is 0700 on this base image).
RUN chmod -R a+rX /opt/uv-tools /opt/uv-python \
    && chmod -R a+rwX /opt/uv-cache /root/snae-build \
    && chmod 711 /root

WORKDIR /root/snae-build
