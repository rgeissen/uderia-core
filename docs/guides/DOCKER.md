# Docker Deployment

Uderia can be deployed in Docker containers for production use, testing, and multi-user scenarios. The application includes built-in support for credential isolation in shared deployments. Versioning, the compose image names and the build-time version stamp are described in the [Release Process](../RELEASE.md); changes per release are in the [Changelog](../CHANGELOG.md).

## Quick Start with Docker

Host sizing is in the install guide's [System requirements](INSTALL.md#system-requirements): the compose file caps the container at 6 GB (`mem_limit`), which covers the 1.5 GB idle footprint, steady use and the peak of a large knowledge-graph rebuild; the image needs about 3 GB for the Python environment plus the embedding model, which the container downloads once at first use.

**Recommended — Docker Compose** (binds `0.0.0.0:5050` and stamps the version into the image):

```bash
# 1. Edit docker-compose.yml first. It is the maintainer's production file:
#    - the `volumes:` host path points at the maintainer's production directory
#    - CORS_ALLOWED_ORIGINS and APP_BASE_URL point at the maintainer's domain
#      (APP_BASE_URL builds the OAuth, SSO and connector callback URLs)
#    - it sets `privileged: true`; review whether your deployment needs that
# 2. Give the container a SECRET_KEY: put SECRET_KEY=... in the .env beside
#    docker-compose.yml, or export it in the shell. An empty value stops the server.
UDERIA_VERSION="$(git describe --tags)" docker compose build
docker compose up -d        # image uderia-v1-27, container uderia-v1-27
```

> **What the image leaves out.** The Dockerfile copies the whole build context (`COPY . .`). `Dockerfile.dockerignore`, which BuildKit (the default builder) reads in preference to any `.dockerignore`, keeps the deployment's secrets and data out of the image: `.env`, `tda_keys/jwt_secret.key`, the provenance signing key, a local licence, `tda_auth.db`, `tda_eval.db`, `tda_sessions/`, `tda_files/`, `rag/`, `backups/` and `logs/`. The legacy builder (`DOCKER_BUILDKIT=0`) does not read it, so build from a clean checkout there. Gate: `test/test_dockerignore.py`.

**Plain `docker run`:** the image's default command already binds `0.0.0.0:5050`. The explicit command below repeats it and adds `--nogitcall`:

```bash
# Build the image
docker build -t uderia:latest .

# Generate a secure SECRET_KEY (required)
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Run the container (bind to all interfaces inside the container)
docker run -d \
  -p 5050:5050 \
  -e SECRET_KEY=$SECRET_KEY \
  uderia:latest \
  python -m trusted_data_agent.main --host 0.0.0.0 --port 5050 --nogitcall
```

> **Important:** The `SECRET_KEY` environment variable is **required**. Without it, the container will fail to start with `ValueError: SECRET_KEY is not set`. CORS allows any origin unless `CORS_ALLOWED_ORIGINS` is set (comma-separated exact origins, e.g. `-e CORS_ALLOWED_ORIGINS=https://uderia.example.com`).

## Multi-User Deployment Considerations

**The application now supports true multi-user authentication with user isolation:**

### Production Deployment (Recommended)
- Single shared container supports multiple simultaneous users
- Each user has their own account with encrypted credentials
- User tiers control access to features (user, developer, admin)
- Session data isolated per user with JWT authentication
- Best for: Production deployments, team collaboration

### Initial Setup:
```bash
# Generate SECRET_KEY if not already set
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

docker run -d \
  -p 5050:5050 \
  -v $(pwd)/tda_auth.db:/app/tda_auth.db \
  -v $(pwd)/tda_eval.db:/app/tda_eval.db \
  -v $(pwd)/tda_sessions:/app/tda_sessions \
  -v $(pwd)/tda_keys:/app/tda_keys \
  -v $(pwd)/tda_files:/app/tda_files \
  -v $(pwd)/tda_skills:/app/tda_skills \
  -v $(pwd)/rag:/app/rag \
  -v $(pwd)/.chromadb_rag_cache:/app/.chromadb_rag_cache \
  -v $(pwd)/backups:/app/backups \
  -v $(pwd)/.env:/app/.env \
  -e SECRET_KEY=$SECRET_KEY \
  uderia:latest \
  python -m trusted_data_agent.main --host 0.0.0.0 --port 5050 --nogitcall
```

Keep `tda_keys/` — it holds the JWT secret and the Ed25519 signing key; losing it invalidates every signature and receipt. `.env` holds `TDA_ENCRYPTION_KEY`, without which stored secrets cannot be decrypted. (Create empty `tda_auth.db`, `tda_eval.db` and `.env` files first if they do not exist yet, so Docker mounts files rather than directories. Run the command from a checkout that has `tda_keys/license.key` and `tda_keys/public_key.pem`: the `tda_keys` mount replaces the image's copy.)

**Important Security Steps:**
1. **Set `SECRET_KEY`** environment variable (required) - used for session management; the app will not start without it
2. Mount volumes for the databases (`tda_auth.db`, `tda_eval.db`), `tda_sessions`, `tda_keys`, `tda_files` (the local file store), `tda_skills`, `rag/` (planner cases), the vector cache, `backups/` (the nightly verified database copies) and `.env` (see above)
3. Change default admin password immediately after first login
4. Create individual user accounts for team members
5. Configure HTTPS reverse proxy (nginx, traefik) for production
6. Set `TDA_ENCRYPTION_KEY` in `.env` before any secret is stored. On an installation that already holds secrets, run `maintenance/rotate_encryption_key.py` instead (setting the key by hand makes existing secrets unreadable)
7. For a hardened deployment (`TDA_SECURITY_PROFILE=hardened`: refuses to start with insecure keys and turns on the 30-minute idle session lock; audit tables are append-only in every mode), follow [SECURITY_ARCHITECTURE.md §13.1](../Architecture/SECURITY_ARCHITECTURE.md)

**Optional Security Configuration:**
- **Rate Limiting**: On by default; configure or switch it off through the web UI:
  - Navigate to **Administration** → **Config** → **Security** → **Rate Limiting**
  - Toggle "Enable Rate Limiting" and configure limits for your deployment
  - Per-IP limits: login attempts per minute, registrations per hour, and API calls per minute for requests that carry no valid credential
  - Per-user limits under Global Override: prompts per hour, prompts per day and configuration changes per hour
  - Changes take effect within 60 seconds (cache refresh)

## Pre-configuring MCP Server

You can bake MCP Server configuration into the Docker image:

1. **Before building**, edit `tda_config.json`:
```json
{
  "mcp_servers": [
    {
      "name": "Your MCP Server",
      "host": "your-host.com",
      "port": "8888",
      "path": "/mcp",
      "id": "default-server-id"
    }
  ],
  "active_mcp_server_id": "default-server-id"
}
```

2. **Build the image** - MCP configuration is included
3. **Users only need to configure LLM credentials** - much simpler onboarding!

## Detailed Security & Credential Documentation

For credential encryption, isolation, and security considerations relevant to containerized deployment, see:
[**Security Architecture (docs/Architecture/SECURITY_ARCHITECTURE.md)**](../Architecture/SECURITY_ARCHITECTURE.md)
