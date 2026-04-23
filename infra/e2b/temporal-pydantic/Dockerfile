# Pydantic AI + Temporal impl
# Bundles the Temporal dev server (single-binary CLI) for in-sandbox use.
# Production HA would use Temporal Cloud — called out in findings.

FROM halfmarathon-base:latest

# Install the Temporal CLI (includes the dev server).
RUN curl -sSf https://temporal.download/cli.sh | sh \
 && mv /root/.temporalio/bin/temporal /usr/local/bin/temporal

# Phase 1 will add `RUN uv sync --extra temporal-pydantic` once that extra exists.

EXPOSE 7233 8233
