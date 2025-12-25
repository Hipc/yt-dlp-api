# ---- ffmpeg stage (static, multi-arch) ----
FROM docker.io/mwader/static-ffmpeg:8.0.1 AS ffmpeg

# ---- builder stage (install python deps into a relocatable dir) ----
FROM docker.io/python:3.13-slim-bookworm AS builder
WORKDIR /app

# Install python deps into /opt/python (NOT a venv)
COPY requirements.txt .
RUN pip install --no-cache-dir --target /opt/python -r requirements.txt

# Copy app code
COPY . .

# ---- hardened runtime stage (no shell) ----
FROM dhi.io/python:3.13
WORKDIR /app

# App
COPY --from=builder /app /app

# Python deps (relocatable)
COPY --from=builder /opt/python /opt/python
ENV PYTHONPATH="/opt/python"

# ffmpeg + ffprobe
COPY --from=ffmpeg /ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

ENV PATH="/usr/local/bin:${PATH}"

# Configurable user (defaults to nonroot 65532 used by Chainguard)
ENV APP_USER=nonroot
ENV APP_UID=65532
ENV APP_GID=65532

# Create user and directories with proper permissions
RUN addgroup -g "${APP_GID}" "${APP_USER}" && \
    adduser -D -u "${APP_UID}" -G "${APP_USER}" "${APP_USER}" && \
    chown -R "${APP_USER}:${APP_USER}" /app

# Create and grant permissions to output directory
RUN mkdir -p /app/downloads && \
    chown -R "${APP_USER}:${APP_USER}" /app/downloads

USER ${APP_USER}

EXPOSE 8000
CMD ["python", "main.py"]
