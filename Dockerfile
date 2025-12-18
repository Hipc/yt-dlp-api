# ---- ffmpeg stage (static, multi-arch) ----
FROM docker.io/mwader/static-ffmpeg:8.0.1 AS ffmpeg

# ---- builder stage (python deps) ----
FROM docker.io/python:3.13-slim-bookworm AS builder
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# ---- hardened runtime stage (no shell) ----
FROM dhi.io/python:3.13
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# Put ffmpeg + ffprobe in PATH
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

ENV PATH="/opt/venv/bin:/usr/local/bin:${PATH}"

EXPOSE 8000
CMD ["python","main.py"]
