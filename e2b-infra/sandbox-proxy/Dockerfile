FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3003

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3003", "--log-level", "info"]
