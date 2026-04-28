FROM ubuntu:24.04

# FUSE support — required by both Apptainer overlay and buildah overlay storage.
# squashfuse enables SquashFS mounts used by Apptainer for OCI images.
# Apptainer — pinned version, installed from the official .deb.
# Handles OCI archive execution: apptainer exec oci-archive:<path> <cmd>
# Both apt blocks are merged into one RUN so the apt lists remain live for the
# .deb install and /var/log/apt/eipp.log.xz is not left stale between layers
# (dpkg opens it with O_CREAT|O_EXCL; a pre-existing file from a prior layer
# causes exit code 2 on NERSC / podman rootless builds).
ARG APPTAINER_VERSION=1.4.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse3 \
    libfuse2t64 \
    squashfuse \
    buildah \
    fakeroot \
    git \
    curl \
    ca-certificates \
    && ARCH="$(dpkg --print-architecture)" \
    && if [ "$ARCH" = "amd64" ]; then \
        curl -fsSL \
            "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
            -o /tmp/apptainer.deb \
        && apt-get install -y /tmp/apptainer.deb \
        && rm /tmp/apptainer.deb; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Python + uv + lightcone-cli.
# LIGHTCONE_VERSION is substituted at render time (lc launch writes a rendered
# copy to .lightcone/containers/claude.Containerfile with the value filled in).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ARG LIGHTCONE_VERSION
# Dev/local builds (e.g. "0.x.y.dev0+gabcdef" or "dev") are not published to
# PyPI, so we cannot pin them. The ARG is still baked in for content-addressed
# tag computation (the image rebuilds when lc is upgraded). For non-release
# strings we install the latest stable release from PyPI instead.
RUN case "${LIGHTCONE_VERSION}" in \
    *dev*|*+*|dev) uv pip install --system lightcone-cli ;; \
    *) uv pip install --system "lightcone-cli==${LIGHTCONE_VERSION}" ;; \
    esac

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
