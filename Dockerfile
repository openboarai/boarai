# syntax=docker/dockerfile:1

# BoarAI — SOURCE channel image (the default `docker compose up -d --build`).
#
# The copied tree IS the install: no `pip install` of the package. The update
# steward classifies a running tree without `.git` as the `source` channel and
# updates it in place by swapping verified release archives; a pip-installed
# copy would be detected as the `pip` channel and an in-container update would
# cross channels (PyPI over a locally built tree) — forbidden by design.
#
# Runtime facts honoured here (see boarai/core):
#   - single port 26620 (HTTP + WS), bind 0.0.0.0 by default (core/server)
#   - data dir anchor: BOARAI_DATA_DIR > ~/.boarai (core/runtime/data_paths)
#   - first-run access key is claimable from loopback only (core/security/gate),
#     so containers seed it via `boarai key init` or BOARAI_API_TOKEN
#   - tiktoken cl100k_base BPE files download on first use
#     (core/prompt/token_counter) — pre-warmed here so runtime needs no network

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BOARAI_DATA_DIR=/data \
    TIKTOKEN_CACHE_DIR=/opt/boarai-tiktoken

WORKDIR /opt/boarai

# Runtime dependencies only (aiohttp / pyyaml / aiofiles / tiktoken).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The source tree (entry main.py + kernel boarai/ + resource tree boar/).
# .git is excluded by .dockerignore, which is what makes this the source
# channel rather than the git channel.
COPY main.py MANIFEST.in pyproject.toml README.md ./
COPY boarai ./boarai
COPY boar ./boar

# Non-root runtime user. The tree stays user-writable: the source channel
# swaps release archives in place and must be able to write here. The uid/gid
# are fixed (and overridable) so a host bind mount for /data can be chowned to
# a predictable owner — the container is NOT root.
ARG BOARAI_UID=10001
ARG BOARAI_GID=10001
RUN set -eu; \
    uid="${BOARAI_UID:-10001}"; \
    gid="${BOARAI_GID:-10001}"; \
    groupadd --system --gid "$gid" boarai; \
    useradd --system --uid "$uid" --gid "$gid" \
            --create-home --shell /usr/sbin/nologin boarai; \
    mkdir -p /data /opt/boarai-tiktoken; \
    chown -R boarai:boarai /data /opt/boarai-tiktoken /opt/boarai

USER boarai

# Bake the cl100k_base encoding into the image; a failed warm-up is not fatal
# (the kernel falls back to a char-based estimate).
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" || true

EXPOSE 26620

# Public endpoint, needs no credential: proves the HTTP edge is serving.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:26620/api/auth/status', timeout=4)"

# Source-channel launch shape (update/steward.server_launch_prefix).
CMD ["python", "main.py"]
