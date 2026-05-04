# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

LABEL org.opencontainers.image.title="Canfar Cutout Worker"
LABEL org.opencontainers.image.description="Slim CANFAR headless runtime for manifest-driven FITS cutout jobs"
LABEL org.opencontainers.image.source="https://github.com/CanDIAPL/canfar-cutout-worker"
LABEL ca.nrc.cadc.skaha.type="headless"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/cutout-worker

COPY requirements.txt /tmp/cutout-worker-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --prefer-binary -r /tmp/cutout-worker-requirements.txt \
    && rm -f /tmp/cutout-worker-requirements.txt

COPY . /opt/cutout-worker

RUN chmod -R a+rX /opt/cutout-worker \
    && python -m py_compile cutout_worker/__main__.py cutout_worker/cli.py

ENV PYTHONPATH=/opt/cutout-worker

CMD ["python", "-m", "cutout_worker", "--help"]
