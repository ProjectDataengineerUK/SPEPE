FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    # curl is needed by the HEALTHCHECK command below
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 spepe

COPY --from=builder /root/.local /home/spepe/.local
COPY --chown=spepe:spepe . .
# ui/static/ is copied above — dashboard HTML is served from inside the container

USER spepe
ENV PATH=/home/spepe/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

EXPOSE 8080

# HEALTHCHECK fix: the previous CMD used shell-variable syntax ($PORT) inside
# a Python string passed to exec-form CMD, which does NOT expand env vars.
# Fix: use /bin/sh -c so the shell expands $PORT at runtime.
# curl is simpler and more reliable than spawning a Python interpreter here.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f "http://localhost:${PORT}/healthz" || exit 1

CMD ["sh", "-c", "chainlit run ui/chainlit_app.py --port=${PORT} --host=0.0.0.0 --headless"]
