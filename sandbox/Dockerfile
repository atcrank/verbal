ARG DOCKER_PYTHON_IMAGE=python:3.12-slim
FROM ${DOCKER_PYTHON_IMAGE}

USER root

# Capture pip mirror configurations from docker-compose build args
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG PIP_FIND_LINKS
ENV PIP_INDEX_URL=$PIP_INDEX_URL
ENV PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST
ENV PIP_FIND_LINKS=$PIP_FIND_LINKS

# Conditionally install OpenMP runtime (libgomp) across Debian/Ubuntu, Red Hat UBI, or Alpine
RUN if command -v apt-get >/dev/null 2>&1; then \
        apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*; \
    elif command -v microdnf >/dev/null 2>&1; then \
        microdnf install -y libgomp && microdnf clean all; \
    elif command -v dnf >/dev/null 2>&1; then \
        dnf install -y libgomp && dnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y libgomp && yum clean all; \
    fi || true

# Ensure an unprivileged user and home directory structure exist (compatible with Debian & Red Hat UBI)
RUN if ! id -u python >/dev/null 2>&1; then \
        useradd -m -u 1000 -s /bin/bash python 2>/dev/null || true; \
    fi && \
    mkdir -p /home/python/app /home/python/workspace && \
    chown -R 1000:0 /home/python 2>/dev/null || true && \
    chmod -R g+rwX /home/python 2>/dev/null || true

WORKDIR /home/python/app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY main.py .

USER 1000

ENV PATH="/home/python/.local/bin:/usr/local/bin:${PATH}"
ENV WORKSPACE_DIR="/home/python/workspace"

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]