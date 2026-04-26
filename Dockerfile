FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip install .

ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "-m", "pvpskill_backend"]
