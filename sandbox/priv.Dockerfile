# ── Stage 1: Build sandbox server ──
FROM rust:1.97-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libseccomp-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy workspace manifest and all source (lock file enables reproducible builds)
COPY Cargo.toml Cargo.lock ./
COPY server server/
COPY lib/sandbox_seccomp lib/sandbox_seccomp/
COPY runtime runtime/

# Build server (uses workspace resolver)
RUN cargo build --release -p sandbox-server

# ── Stage 2: Build seccomp shared libs ──
FROM rust:1.97-slim-bookworm AS seccomp

RUN apt-get update && apt-get install -y --no-install-recommends \
    libseccomp-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY lib/sandbox_seccomp ./

RUN cargo build --release --features python3 && \
    mv target/release/libsandbox.so /libpython.so

RUN cargo clean && \
    cargo build --release --features nodejs && \
    mv target/release/libsandbox.so /libnodejs.so

# ── Stage 3: Runtime (privileged) ──
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm libseccomp2 ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

# Seccomp shared libs (loaded at runtime by prescript)
COPY --from=seccomp /libpython.so /usr/local/share/sandbox/libpython.so
COPY --from=seccomp /libnodejs.so /usr/local/share/sandbox/libnodejs.so

# Server binary
COPY --from=builder /build/target/release/sandbox-server /usr/local/bin/sandbox-server

# Config and runtime assets
COPY runtime/config.toml /etc/sandbox/config.toml
COPY runtime /usr/local/share/sandbox/

RUN mkdir -p /usr/local/share/sandbox/tmp && chmod 1777 /usr/local/share/sandbox/tmp

# Install Node.js koffi FFI library (required by prescript.js for seccomp)
RUN cd /usr/local/share/sandbox && npm install koffi@3

# Sandbox user + dedicated non-privileged group for privilege dropping
RUN groupadd -g 65537 sandbox && useradd -u 65537 -g 65537 sandbox

ENV CONFIG_PATH=/etc/sandbox/config.toml

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8194/health || exit 1

EXPOSE 8194
CMD ["sandbox-server"]
