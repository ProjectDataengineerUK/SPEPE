FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    gfortran \
    libgeos-dev \
    libproj-dev \
    libgdal-dev \
    libopenblas-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# uv resolves deps orders of magnitude faster and avoids ResolutionTooDeep
RUN pip install --no-cache-dir uv

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-app.txt .
RUN uv pip install --no-cache -r requirements-app.txt


FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 spepe

COPY --from=builder /opt/venv /opt/venv
COPY --chown=spepe:spepe . .
RUN chown -R spepe:spepe /app

USER spepe
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f "http://localhost:${PORT}/healthz" || exit 1

CMD ["sh", "-c", "chainlit run ui/chainlit_app.py --port=${PORT} --host=0.0.0.0 --headless"]
