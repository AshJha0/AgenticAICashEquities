# syntax=docker/dockerfile:1
# Python 3.12 on a digest-pinned base (python:3.12-slim-bookworm, digest read 2026-11-07).
# Dependencies, including the [llm] extra, come only from the hashed lock; the image runs as a
# non-root user. Nothing in the image calls a model unless CEAP_LLM_PROVIDER=anthropic and a key
# are supplied at run time.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
# the project itself has no hash; its dependencies were installed above, so --no-deps
RUN pip install --no-deps ".[llm]" \
    && useradd --create-home --uid 10001 ceap \
    && chown -R ceap:ceap /app

ENV CEAP_ENV=prod \
    CEAP_AUTO_APPROVE=false \
    CEAP_LLM_PROVIDER=mock     CEAP_PREWARM_DATASETS=T01

USER ceap
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "ceap.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
