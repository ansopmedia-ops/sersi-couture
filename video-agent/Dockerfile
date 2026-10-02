# Render service for n8n / HTTP callers:  docker build -t script2video . && docker run -p 8000:8000 script2video
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core espeak-ng \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY script2video ./script2video
COPY server.py .
ENV PORT=8000 JOBS_DIR=/data/jobs PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["python", "server.py"]
