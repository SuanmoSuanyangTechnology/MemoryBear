FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "docker>=7.2.0" \
    "fastapi>=0.119.0" \
    "uvicorn>=0.37.0" \
    "redis>=5.0.0" \
    "httpx>=0.28.0" \
    "pydantic>=2.12.0" \
    "pydantic-settings>=2.0.0" \
    "python-dotenv>=1.1.0"

COPY app/ ./app/

EXPOSE 3001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
