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
        useradd -m -u 1000 -g 0 -s /bin/bash python 2>/dev/null || useradd -m -u 1000 -s /bin/bash python 2>/dev/null || true; \
    fi && \
    mkdir -p /home/python/app /home/python/workspace && \
    chown -R 1000:0 /home/python 2>/dev/null || true && \
    chmod -R g+rwX /home/python 2>/dev/null || true

# Ensure python symlink exists (compatible with RHEL/UBI minimal where only python3 exists)
RUN if ! command -v python >/dev/null 2>&1; then \
        if command -v python3 >/dev/null 2>&1; then \
            ln -sf "$(command -v python3)" /usr/local/bin/python; \
        fi; \
    fi

WORKDIR /home/python/app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# If a base virtual environment exists (e.g. /opt/app-root in Red Hat UBI), ensure user 1000 and group 0 have full access
RUN if [ -d /opt/app-root ]; then \
        chown -R 1000:0 /opt/app-root 2>/dev/null || true; \
        chmod -R a+rX /opt/app-root 2>/dev/null || true; \
        chmod -R g+rwX /opt/app-root 2>/dev/null || true; \
    fi

COPY main.py .
RUN chown -R 1000:0 /home/python/app 2>/dev/null || true

USER 1000

ENV HOME="/home/python"
ENV PATH="/opt/app-root/bin:/opt/app-root/src/bin:/home/python/.local/bin:/usr/local/bin:${PATH}"
ENV WORKSPACE_DIR="/home/python/workspace"

# Clear any inherited ENTRYPOINT (such as Red Hat UBI's container-entrypoint script)
# so uvicorn runs directly without S2I or virtualenv activation errors
ENTRYPOINT []

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]