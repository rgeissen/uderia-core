# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Deployed version stamp. The slim base image has no git binary, so the running
# container cannot derive its version from .git at runtime. Instead the release
# tag is baked in at build time:
#   docker build --build-arg UDERIA_VERSION="$(git describe --tags)" ...
# get_app_version() reads UDERIA_VERSION first, so the info modal reports exactly
# what was built. If the arg is omitted it falls back to the pyproject version.
ARG UDERIA_VERSION=""
ENV UDERIA_VERSION=${UDERIA_VERSION}

# Copy the entire project context. Dockerfile.dockerignore (read by BuildKit, the default
# builder, in preference to any .dockerignore) keeps .env, the JWT secret, the signing key
# and every database out of the image. Gate: test/test_dockerignore.py
COPY . .

# Create tda_keys directory for persistent key storage
# JWT secret will be auto-generated on first run if not provided via TDA_JWT_SECRET_KEY env var
RUN mkdir -p /app/tda_keys && chmod 700 /app/tda_keys

# Install the required packages from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- FIX for Unicode Error ---
# Remove the UTF-8 BOM (invisible characters) from the main script if it exists.
RUN sed -i '1s/^\xEF\xBB\xBF//' src/trusted_data_agent/main.py

# Install the application in editable mode to make the 'src' package discoverable
RUN pip install -e .

# --- Environment Variables ---
# TDA_TTS_CREDENTIALS (optional): Google service account JSON string to bootstrap global TTS.
#   On first startup, if tts_mode is 'disabled', credentials are encrypted and stored in the DB,
#   and tts_mode is set to 'global'. The env var is not needed after initial bootstrap.
#   Example: docker run -e TDA_TTS_CREDENTIALS='{"type":"service_account","project_id":"..."}' ...
# GOOGLE_APPLICATION_CREDENTIALS (optional): File path to a Google service account JSON file.
#   Used as fallback if TDA_TTS_CREDENTIALS is not set. Mount the file into the container.
#   Example: docker run -v /path/to/creds.json:/app/tts-creds.json -e GOOGLE_APPLICATION_CREDENTIALS=/app/tts-creds.json ...

# Make port 5050 available to the world outside this container
EXPOSE 5050

# The command to run when the container launches. Bind every interface: the app's default
# (127.0.0.1) is unreachable through `docker run -p 5050:5050`. docker-compose.yml passes the
# same flags explicitly.
CMD ["python", "-m", "trusted_data_agent.main", "--host", "0.0.0.0", "--port", "5050"]

