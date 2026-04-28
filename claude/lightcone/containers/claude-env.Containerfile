FROM ubuntu:24.04

# FUSE support — required by both Apptainer overlay and buildah overlay storage.
# squashfuse enables SquashFS mounts used by Apptainer for OCI images.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse3 \
    libfuse2 \
    squashfuse \
    buildah \
    fakeroot \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Apptainer — pinned version, installed from the official .deb.
# Handles OCI archive execution: apptainer exec oci-archive:<path> <cmd>
# Use `apt-get install -y ./file.deb` instead of `dpkg -i` so apt resolves
# any remaining dependencies automatically.
ARG APPTAINER_VERSION=1.4.0
RUN curl -fsSL \
    "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
    -o /tmp/apptainer.deb \
    && apt-get install -y /tmp/apptainer.deb \
    && rm /tmp/apptainer.deb

# Python + uv + lightcone-cli.
# LIGHTCONE_VERSION is substituted at render time (lc launch writes a rendered
# copy to .lightcone/containers/claude.Containerfile with the value filled in).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ARG LIGHTCONE_VERSION
RUN uv pip install --system "lightcone-cli==${LIGHTCONE_VERSION}"

# Node.js LTS + Claude Code CLI
ARG NODE_VERSION=22
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Marker read by lc build / lc run to detect containerized operation.
ENV LIGHTCONE_CONTAINER=1

WORKDIR /workspace
ENTRYPOINT ["claude"]
