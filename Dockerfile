FROM python:3.12-slim

WORKDIR /app
COPY . .

# 下载静态编译的 ffmpeg，避免 apt-get update
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m pip install --no-cache-dir static-ffmpeg && \
    static_ffmpeg_paths

EXPOSE 8000

CMD ["python","main.py"]