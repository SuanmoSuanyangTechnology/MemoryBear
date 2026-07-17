# ── Stage 1: Build sandbox server ──
FROM rust:1.97-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libseccomp-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY Cargo.toml Cargo.lock ./
COPY server server/
COPY lib/sandbox_seccomp lib/sandbox_seccomp/
COPY runtime runtime/

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

# ── Stage 3: Runtime (non-privileged) ──
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm libseccomp2 ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

# Seccomp shared libs
COPY --from=seccomp /libpython.so /usr/local/share/sandbox/libpython.so
COPY --from=seccomp /libnodejs.so /usr/local/share/sandbox/libnodejs.so

# Server binary
COPY --from=builder /build/target/release/sandbox-server /usr/local/bin/sandbox-server

# Config and runtime assets
COPY runtime/config.toml /etc/sandbox/config.toml
COPY runtime /usr/local/share/sandbox/

# Override config for non-privileged mode
RUN sed -i 's/privilege = true/privilege = false/' /etc/sandbox/config.toml && \
    sed -i 's/sandbox_uid = 65537/sandbox_uid = 1000/' /etc/sandbox/config.toml && \
    sed -i 's/sandbox_gid = 65537/sandbox_gid = 1000/' /etc/sandbox/config.toml

RUN mkdir -p /usr/local/share/sandbox/tmp && chmod 1777 /usr/local/share/sandbox/tmp

# Pre-install Python dependencies at build time (no runtime pip install needed).
# No env.sh needed — Landlock restricts filesystem access without chroot.
RUN pip install --upgrade -r /usr/local/share/sandbox/requirements.txt

# Install Node.js koffi FFI library
RUN cd /usr/local/share/sandbox && npm install koffi@3

# Regular user instead of high arbitrary uid (drop_privileges is skipped)
RUN groupadd -g 1000 sandbox && useradd -u 1000 -g 1000 sandbox

ENV CONFIG_PATH=/etc/sandbox/config.toml
ENV PRIVILEGE=false

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8194/health || exit 1

USER sandbox
EXPOSE 8194
CMD ["sandbox-server"]
