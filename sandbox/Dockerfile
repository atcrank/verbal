ARG DOCKER_PYTHON_IMAGE=python:3.12-slim
FROM ${DOCKER_PYTHON_IMAGE}

# Capture pip mirror configurations from docker-compose build args
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ENV PIP_INDEX_URL=$PIP_INDEX_URL
ENV PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST

# Ensure unprivileged 'python' user and home directory structure exist.
# If starting from a root image, provision user 'python' and set permissions.
# If starting from an image already configured with USER python, proceed gracefully.
RUN if [ "$(id -u)" = "0" ]; then \
        id -u python >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash python; \
        mkdir -p /home/python/app /home/python/workspace; \
        chown -R python:python /home/python; \
    fi

WORKDIR /home/python/app

COPY --chown=python:python requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt || pip install --no-cache-dir -r requirements.txt

COPY --chown=python:python main.py .

USER python

ENV PATH="/home/python/.local/bin:${PATH}"
ENV WORKSPACE_DIR="/home/python/workspace"

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]