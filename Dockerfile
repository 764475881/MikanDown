FROM python:3.12-slim

WORKDIR /app

# Install system deps for curl_cffi compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Cache pip deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Data volume
RUN mkdir -p /app/data
VOLUME /app/data

EXPOSE 5000

CMD ["python", "main.py"]
