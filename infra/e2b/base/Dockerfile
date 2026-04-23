# halfmarathon base sandbox image
# Python 3.12 + uv + repo bind mount expected at /workspace at runtime.
# Implementation images extend this with their specific deps.

FROM e2bdev/code-interpreter:latest

# uv is the fastest Python package manager and the one we standardize on.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv

# Standard tools we want available in every sandbox.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        jq \
        sqlite3 \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/*

# Default workdir; the actual repo is bind-mounted here at runtime.
WORKDIR /workspace

# Cache dir for uv to share across runs in the same sandbox.
ENV UV_CACHE_DIR=/root/.cache/uv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# tini handles PID 1 properly (matters for our long-running agents).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
