FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLIPFORGE_WORK=/data/work \
    CLIPFORGE_OUTPUT=/data/clips \
    CLIPFORGE_HOST=0.0.0.0 \
    CLIPFORGE_PORT=8000

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY clipforge ./clipforge
COPY web ./web

RUN mkdir -p /data/work /data/clips
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

# $PORT is set by Render / Railway / Fly; 8000 is the local default.
CMD ["sh", "-c", "uvicorn clipforge.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
