FROM python:3.12-slim
USER root
SHELL ["/bin/bash", "-c"]

ARG NEED_MIRROR=1

WORKDIR /code

# 1. Download dependencies through download_deps.py: python download_deps.py --china-mirrors
# 2. Copy models
COPY huggingface.co/InfiniFlow/deepdoc/ /code/res/deepdoc/
COPY huggingface.co/InfiniFlow/text_concat_xgb_v1.0/ /code/res/text_concat_xgb_v1.0/
COPY huggingface.co/InfiniFlow/huqie/huqie.txt.trie /code/res/

# https://github.com/chrismattmann/tika-python
# 3. This is the only way to run python-tika without internet access. Without this set, the default is to check the tika version and pull latest every time from Apache.
COPY nltk_data/ /root/nltk_data/
COPY tika-server-standard-3.1.0.jar /tmp/tika-server.jar
COPY tika-server-standard-3.1.0.jar.md5 /tmp/tika-server.jar.md5
COPY cl100k_base.tiktoken /code/res/9b5ad71b2ce5302211f9c61530b329a4922fc6a4

ENV TIKA_SERVER_JAR="file:///tmp/tika-server.jar"
ENV DEBIAN_FRONTEND=noninteractive

# 4. Setup apt
# Python package and implicit dependencies:
# opencv-python: libglib2.0-0 libglx-mesa0 libgl1
# libreoffice: libreoffice libreoffice-writer libreoffice-impress fonts-wqy-zenhei fonts-noto-cjk
# python-docx:   default-jdk                              tika-server-standard-3.0.0.jar
# Building C extensions: libpython3-dev libgtk-4-1 libnss3 xdg-utils libgbm-dev
RUN --mount=type=cache,id=mem_apt,target=/var/cache/apt,sharing=locked \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|http://ports.ubuntu.com|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list; \
        sed -i 's|http://archive.ubuntu.com|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    chmod 1777 /tmp && \
    apt update && \
    apt --no-install-recommends install -y ca-certificates && \
    apt update && \
    apt install -y libglib2.0-0 libglx-mesa0 libgl1 && \
    apt install -y libreoffice libreoffice-writer libreoffice-impress fonts-wqy-zenhei fonts-noto-cjk && \
    apt install -y default-jdk && \
    apt install -y libpython3-dev libgtk-4-1 libnss3 xdg-utils libgbm-dev && \
    apt install -y libjemalloc-dev && \
    apt install -y python3-pip pipx nginx unzip curl wget git vim less && \
    apt install -y ghostscript

RUN if [ "$NEED_MIRROR" == "1" ]; then \
        pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
        pip3 config set global.trusted-host pypi.tuna.tsinghua.edu.cn; \
        mkdir -p /etc/uv && \
        echo "[[index]]" > /etc/uv/uv.toml && \
        echo 'url = "https://pypi.tuna.tsinghua.edu.cn/simple"' >> /etc/uv/uv.toml && \
        echo "default = true" >> /etc/uv/uv.toml; \
    fi; \
    pipx install uv

ENV PYTHONDONTWRITEBYTECODE=1 DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
ENV PATH=/root/.local/bin:$PATH


# 5. install dependencies from uv.lock file
COPY ./pyproject.toml /code/pyproject.toml
COPY ./uv.lock /code/uv.lock
COPY ./app /code/app
COPY ./alembic.ini /code/alembic.ini
COPY ./migrations /code/migrations

# https://github.com/astral-sh/uv/issues/10462
# uv records index url into uv.lock but doesn't failover among multiple indexes
RUN --mount=type=cache,id=mem_uv,target=/root/.cache/uv,sharing=locked \
    if [ "$NEED_MIRROR" == "1" ]; then \
        sed -i 's|pypi.org|pypi.tuna.tsinghua.edu.cn|g' uv.lock; \
    else \
        sed -i 's|pypi.tuna.tsinghua.edu.cn|pypi.org|g' uv.lock; \
    fi; \
    uv lock && \
    uv sync --locked --no-dev

ENV PATH=/code/.venv/bin:$PATH



