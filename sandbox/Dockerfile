ARG DOCKER_PYTHON_IMAGE=python:3.12-slim
FROM ${DOCKER_PYTHON_IMAGE}

USER root

# Capture pip mirror configurations from docker-compose build args
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG PIP_FIND_LINKS

# Conditionally install OpenMP runtime (libgomp) across Debian/Ubuntu, Red Hat UBI, or Alpine
RUN (command -v apt-get >/dev/null 2>&1 && apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*) \
 || (command -v microdnf >/dev/null 2>&1 && microdnf install -y libgomp && microdnf clean all) \
 || (command -v dnf >/dev/null 2>&1 && dnf install -y libgomp && dnf clean all) \
 || (command -v yum >/dev/null 2>&1 && yum install -y libgomp && yum clean all) \
 || echo "WARNING: libgomp not installed; pyAgrum may fail at import"

# Build dedicated sandbox virtualenv and install dependencies without activation scripts
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/sandbox \
 && PIP_INDEX_URL="$PIP_INDEX_URL" PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST" PIP_FIND_LINKS="$PIP_FIND_LINKS" \
    /opt/sandbox/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt \
 && chmod -R a+rX /opt/sandbox

WORKDIR /opt/sandbox-app
COPY main.py .

# Run as non-root user (id 1000, root group 0 for container runtime and compose group_add compatibility)
USER 1000:0

ENV HOME=/tmp \
    WORKSPACE_DIR=/home/python/workspace \
    PATH=/opt/sandbox/bin:$PATH

ENTRYPOINT []
EXPOSE 8000
CMD ["/opt/sandbox/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]