FROM python:3.13

WORKDIR /app
COPY . .

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* &&\
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["python","main.py"]