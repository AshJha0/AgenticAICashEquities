# syntax=docker/dockerfile:1
# Python 3.12 on a digest-pinned base (python:3.12-slim-bookworm, digest read 2026-11-07).
# Two stages (0.9.0, G13): the build stage installs the hashed lock and the project into a virtual
# environment; the runtime stage copies only that environment and the data, then removes pip, so the
# running image cannot install anything. Dependencies, including the [llm] extra, come only from the
# hashed lock; the image runs as a non-root user. Nothing in the image calls a model unless
# CEAP_LLM_PROVIDER=anthropic and a key are supplied at run time.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

FROM ${PYTHON_IMAGE} AS build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/ceap
ENV PATH=/opt/ceap/bin:$PATH
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
# the project itself has no hash; its dependencies were installed above, so --no-deps
RUN pip install --no-deps ".[llm]" \
    && pip uninstall -y pip setuptools wheel || true \
    && rm -f /opt/ceap/bin/pip /opt/ceap/bin/pip3*

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/ceap/bin:$PATH
# no pip in the runtime image: drop the base image's own copy too
RUN python -m pip uninstall -y pip setuptools wheel || true \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3* \
    && useradd --create-home --uid 10001 ceap \
    && mkdir -p /var/lib/ceap && chown ceap:ceap /var/lib/ceap
COPY --from=build /opt/ceap /opt/ceap
WORKDIR /app
COPY --chown=ceap:ceap data ./data

ENV CEAP_ENV=prod \
    CEAP_AUTO_APPROVE=false \
    CEAP_LLM_PROVIDER=mock \
    CEAP_PREWARM_DATASETS=T01

USER ceap
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "ceap.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
