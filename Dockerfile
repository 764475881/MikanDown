FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install system deps for curl_cffi compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install deps via uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy app
COPY . .

# Data volume
RUN mkdir -p /app/data
VOLUME /app/data

EXPOSE 5000

CMD ["python", "main.py"]
