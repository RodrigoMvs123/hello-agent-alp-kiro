FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY hello-agent-alp-kiro/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY hello-agent-alp-kiro/ .
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}"]
