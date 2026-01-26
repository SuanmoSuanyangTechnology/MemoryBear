FROM python:3.12-slim
USER root
WORKDIR /code
LABEL authors="Eterntiy"

ARG NEED_MIRROR=0

RUN --mount=type=cache,id=mem_apt,target=/var/cache/apt,sharing=locked \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|https://ports.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list; \
        sed -i 's|https://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    chmod 1777 /tmp && \
    apt update && \
    apt --no-install-recommends install -y ca-certificates && \
    apt update && \
    apt install -y python3-pip pipx nginx unzip curl wget git vim less && \
    apt-get install -y --no-install-recommends tzdata libseccomp2 libseccomp-dev && \
    ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    apt install -y cargo

COPY ./app /code/app
COPY ./dependencies /code/dependencies
COPY ./lib /code/lib
COPY ./script /code/script
COPY ./config.yaml /code/config.yaml
COPY ./main.py /code/main.py
COPY ./requirements.txt /code/requirements.txt

RUN python -m venv .venv
RUN .venv/bin/python3 -m pip install -r requirements.txt

RUN cargo build --release --manifest-path lib/seccomp_python/Cargo.toml

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl 127.0.0.1:8194/health


CMD [".venv/bin/python3", "main.py"]