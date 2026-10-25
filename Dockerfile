# Ember Atlas: one image serving the API and the built web app.
# Stage 1: build the frontend bundle.
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN node build.mjs

# Stage 2: Python runtime.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    EMBER_FRONTEND_DIST=/app/frontend/dist \
    EMBER_ARTIFACTS_DIR=/app/artifacts
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/README.md backend/
COPY backend/src backend/src
RUN pip install --no-cache-dir "./backend[postgis]"
COPY fixtures fixtures
COPY --from=web /web/dist /app/frontend/dist
RUN mkdir -p /app/artifacts /app/data && useradd -m ember && chown -R ember /app
USER ember
EXPOSE 8000
CMD ["sh", "-c", "ember migrate && ember serve --host 0.0.0.0 --port 8000"]
