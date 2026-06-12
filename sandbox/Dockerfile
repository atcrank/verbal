FROM python:3.13-slim

# Capture pip mirror configurations from docker-compose build args
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ENV PIP_INDEX_URL=$PIP_INDEX_URL
ENV PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST

# Create a low-privileged user for executing code
RUN useradd -m -s /bin/bash sandboxuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Prepare the shared volume mount point
RUN mkdir -p /workspace && chown sandboxuser:sandboxuser /workspace

USER sandboxuser
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]