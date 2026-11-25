# syntax=docker/dockerfile:1

# BoarAI — SOURCE channel image: the copied tree IS the install (no pip install
# of the package), so the update steward classifies it as `source` and updates
# it in place by swapping verified release archives. .git is excluded by
# .dockerignore, which is what makes this the source channel rather than git.

FROM python:3.12-slim

# tiktoken encoding cache lives in the data dir: downloaded once, reused
# across rebuilds instead of re-fetched per container.
ENV PYTHONUNBUFFERED=1 \
    BOARAI_DATA_DIR=/data \
    TIKTOKEN_CACHE_DIR=/data/.tiktoken

WORKDIR /opt/boarai

# Runtime dependencies only (aiohttp / pyyaml / aiofiles / tiktoken).
COPY requirements.txt ./
# pip cache mount: wheels persist between builds, this layer is cheap after
# the first build (COPY layers cache on file checksums anyway).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# The source tree (entry main.py + kernel boarai/ + resource tree boar/).
COPY main.py MANIFEST.in pyproject.toml README.md ./
COPY boarai ./boarai
COPY boar ./boar

CMD ["python", "main.py"]
