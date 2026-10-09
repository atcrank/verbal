ARG DOCKER_PYTHON_IMAGE=python:3.12-slim
FROM ${DOCKER_PYTHON_IMAGE}

# Capture pip mirror configurations from docker-compose build args
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ENV PIP_INDEX_URL=$PIP_INDEX_URL
ENV PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST

# Ensure unprivileged 'python' user and home directory structure exist.
# Also install libgomp1 required by OpenMP extensions (pyAgrum, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* && \
    if [ "$(id -u)" = "0" ]; then \
        id -u python >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash python; \
        mkdir -p /home/python/app /home/python/workspace; \
        chown -R python:python /home/python; \
    fi


WORKDIR /home/python/app

COPY --chown=python:python requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=python:python main.py .

USER python

ENV PATH="/home/python/.local/bin:/usr/local/bin:${PATH}"
ENV WORKSPACE_DIR="/home/python/workspace"

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]