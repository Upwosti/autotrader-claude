FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    gcc g++ libffi-dev libssl-dev \
    curl wget git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install psycopg2-binary redis psutil fastapi uvicorn

COPY . .

# Create required directories
RUN mkdir -p logs local_db local_db/ml_models models

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "watchdog.py", "--hours", "0"]
