FROM python:3.12-slim-bookworm

# FUSE support — required by both Apptainer overlay and buildah overlay storage.
# squashfuse enables SquashFS mounts used by Apptainer for OCI images.
# Apptainer — pinned version, installed from the official .deb.
# unzip — required by the OpenCode install script on Linux.
# Note: we pin -bookworm explicitly because plain `python:3.12-slim` flipped to
# Debian 13 (Trixie) where libfuse2 was renamed to libfuse2t64 and would break
# this apt block.  Bookworm + libfuse2 is what apptainer 1.4 expects.
ARG APPTAINER_VERSION=1.4.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse3 \
    libfuse2 \
    squashfuse \
    buildah \
    fakeroot \
    git \
    curl \
    ca-certificates \
    build-essential \
    unzip \
    && ARCH="$(dpkg --print-architecture)" \
    && if [ "$ARCH" = "amd64" ]; then \
        curl -fsSL \
            "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
            -o /tmp/apptainer.deb \
        && apt-get install -y /tmp/apptainer.deb \
        && rm /tmp/apptainer.deb; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# uv + lightcone-cli.
# LIGHTCONE_VERSION is substituted at render time (lc launch writes a rendered
# copy to .lightcone/containers/lightcone-sandbox.Containerfile).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ARG LIGHTCONE_VERSION
# Dev/local builds are not published to PyPI; for non-release strings we
# install the latest stable release from PyPI.
RUN case "${LIGHTCONE_VERSION}" in \
    *dev*|*+*|dev) uv pip install --system --break-system-packages lightcone-cli ;; \
    *) uv pip install --system --break-system-packages "lightcone-cli==${LIGHTCONE_VERSION}" ;; \
    esac

# Node.js LTS — required by Claude Code (npm) and OpenCode (npm).
ARG NODE_VERSION=22
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Marker read by lc build / lc run to detect containerized operation.
ENV LIGHTCONE_CONTAINER=1

WORKDIR /workspace
ENTRYPOINT ["bash"]
