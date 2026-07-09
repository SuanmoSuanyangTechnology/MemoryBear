FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI only (daemon is accessed via mounted socket)
RUN curl -fsSL https://download.docker.com/linux/static/stable/$(uname -m)/docker-26.1.4.tgz \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    || echo "Docker CLI install skipped (will use mounted socket)"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3002

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3002", "--log-level", "info"]
