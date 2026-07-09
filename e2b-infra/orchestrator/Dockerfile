FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for managing sandbox containers via host Docker socket)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then ARCH="aarch64"; fi && \
    curl -fsSL "https://download.docker.com/linux/static/stable/${ARCH}/docker-26.1.4.tgz" \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

# NOTE: Phase 1 uses Docker as sandbox backend.
# Firecracker (Phase 2) is NOT installed here — it requires x86_64 Linux with KVM.
# When ready for Phase 2, uncomment and adjust for your architecture:
# ARG FC_VERSION=1.7.0
# RUN curl -fsSL "https://github.com/firecracker-microvm/firecracker/releases/download/v${FC_VERSION}/firecracker-v${FC_VERSION}-$(uname -m).tgz" \
#     | tar -xz -C /usr/local/bin --strip-components=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /var/e2b/templates /var/e2b/sandboxes /tmp/e2b-sockets

EXPOSE 3001

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001", "--log-level", "info"]
