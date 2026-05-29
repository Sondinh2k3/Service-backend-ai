# ===========================
# 1. Builder stage
# ===========================
# Build context = parent directory containing 3 sibling projects:
#   ai-algorithm-service/  ← runtime service (this Dockerfile)
#   traffic_rl_features/   ← shared library (editable install)
#   bundle-tooling/        ← ops composer cần để build runtime bundle
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy shared libraries to match uv relative paths (../traffic_rl_features, ../bundle-tooling).
COPY traffic_rl_features /traffic_rl_features
COPY bundle-tooling /bundle-tooling

# Service manifest + lock
COPY ai-algorithm-service/pyproject.toml ai-algorithm-service/uv.lock* ./

# Install dependencies. uv resolves `traffic-rl-features` via local path declared
# in pyproject (tool.uv.sources hoặc PEP 621 dependency-groups path).
RUN if [ -f uv.lock ]; then uv sync --frozen --no-install-project; else uv sync --no-install-project; fi

# Service source
COPY ai-algorithm-service/src /app/src
COPY ai-algorithm-service/api_docs /app/api_docs
COPY ai-algorithm-service/scripts /app/scripts


# ===========================
# 2. Runtime stage
# ===========================
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

WORKDIR /app

COPY --from=builder /root/.local /root/.local
COPY --from=builder /app/.venv /app/.venv

COPY --from=builder /traffic_rl_features /traffic_rl_features
COPY --from=builder /bundle-tooling /bundle-tooling
COPY --from=builder /app/src /app/src
COPY --from=builder /app/api_docs /app/api_docs
COPY --from=builder /app/scripts /app/scripts

# Local Model Storage will be mounted as volume.
RUN mkdir -p /app/models

EXPOSE 8000 8002

# Default command runs combined role; docker-compose overrides per container.
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
