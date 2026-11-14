# 📦 Installation and Setup Guide

## Prerequisites

### System requirements

Uderia is one Python process: the web server, the agents, the local vector store (ChromaDB) and the local graph store (SQLite) run inside it. Size the host for that process plus whatever runs beside it.

| | Minimum | Reference deployment (the maintainer's production host) |
|---|---|---|
| Processor | 2 cores, x86_64 or Apple Silicon; no GPU | 4 cores, x86_64 |
| Memory | 4 GB available to the server process | 6 GB container limit (`mem_limit` in `docker-compose.yml`); about 1.5 GB idle (embedding model and graph caches loaded), 2.5 GB in steady use, peaks of about 0.75 GB above idle while a large knowledge graph is rebuilt or deployed |
| Disk | 5 GB: the Python environment (about 2.7 GB, of which PyTorch, Transformers and ONNX Runtime are the bulk), the embedding model (about 90 MB, downloaded once into the Hugging Face cache), `tda_auth.db`, `tda_sessions/`, `rag/`, `tda_files/` | grows with use: after a year the user database is about 1 GB and the sessions about 400 MB; the schema-ledger backups (`backups/`, on by default) hold several GB and can be pruned |
| Operating system | Linux, macOS or Windows for a native install (the guide below covers venv and conda on each); Linux for the container | Debian, Docker Engine 29, Compose v5 |
| Software | Python 3.12 and `pip`, or Docker Engine with Compose v2 (`docker compose`, BuildKit) | the `python:3.12-slim` image |
| Network | outbound HTTPS to the chosen LLM provider; outbound HTTPS to `huggingface.co` once, for the embedding model (or a pre-populated `~/.cache/huggingface`); a route to the MCP server; port 5050 inbound from the users' browsers (put a reverse proxy in front for TLS) | |
| Browser | a current Chrome, Edge, Firefox or Safari (the interface uses ES modules and server-sent events) | |

**What adds to this.** A local model server (Ollama, or MLX on Apple Silicon only) runs beside Uderia and needs the memory its model needs. The optional external backends — a Teradata or Qdrant vector store, a Neo4j graph store, a PostgreSQL session backend or deployment target, an SMTP relay for e-mail — are separate services with their own requirements; none is needed to start. Attachment reading with OCR (`rapidocr-onnxruntime`, off by default) adds about 25 MB to the environment (ONNX Runtime is already in it).

* **Python 3.12** and `pip` — the version the production image runs (`python:3.12-slim`); the code uses 3.12-only APIs, so an older Python fails at runtime.

* Access to a running **MCP Server**.

* An **API Key** for any supported provider — **Google**, **Anthropic**, **OpenAI**, **Azure OpenAI**, **AWS Bedrock**, **OpenRouter**, **Friendli.AI**, **xAI**, **Celeris** — or a local **Ollama** / [**MLX**](../MLX/README.md) installation.

  * You can obtain a Gemini API key from the [Google AI Studio](https://aistudio.google.com/app/apikey).

  * You can obtain a Claude API key from the [Anthropic Console](https://console.anthropic.com/dashboard).

  * For **Azure**, you will need an **Azure OpenAI Endpoint**, **API Key**, **API Version**, and a **Model Deployment Name**.

  * For AWS, you will need an **AWS Access Key ID**, **Secret Access Key**, and the **Region** for your Bedrock service.

  * You can obtain a Friendli.AI API key from the [Friendli Suite](https://suite.friendli.ai/).

  * For Ollama, download and install it from [ollama.com](https://ollama.com/) and pull a model (e.g., `ollama pull llama3.2`).

### First start

The server serves as soon as its database is ready (about twenty seconds on a laptop). Two things then run in the background, and the log reports each with its duration:

1. **The embedding model** (`all-MiniLM-L6-v2`, `RAG_EMBEDDING_MODEL`). The first start downloads it once from Hugging Face (about 90 MB, into `~/.cache/huggingface`); every later start loads it from the cache in a few seconds. Until it is in memory, `GET /api/status` reports `"knowledge": {"state": "loading"}` and the header's knowledge lane reads Connecting; a question that needs a knowledge repository or the semantic tool ranker waits for it. An air-gapped host pre-populates the cache from a machine that has it.
2. **The bundled Uderia Expert agent.** The public core ships `agent_packs/uderia_expert/bundled/uderia_expert.agentpack`, the @UDERIA documentation assistant built at export time from the documentation in this very distribution, with its chunks and vectors already computed. On the first start it is installed for the admin account, once: loading its vectors takes about four seconds and nothing is embedded on your machine. A deployment that already has a Uderia Expert, or whose admin removed it, is never touched (the seed records itself in the system setting `bundled_packs_seeded`). The agent needs an LLM configuration before it answers; the welcome screen says so.

Neither step blocks the start, and neither repeats. The log lines to look for: `Loading the embedding model …`, `Embedding model ready in … s`, `Bundled pack 'Uderia Expert' installed for admin: …`.

## Step 1: Clone the Repository

```
git clone https://github.com/rgeissen/uderia-core.git
cd uderia-core

```

## Step 2: Set Up the Python Environment

It is highly recommended to use a Python virtual environment.

**Quickest (macOS, conda):** `bash maintenance/setup_python312_env.sh` creates or updates a `uderia312` environment, installs everything, and verifies it; then `conda activate uderia312`.

**Option A: Using Python venv**

1. **Create and activate a virtual environment:**

   ```
   # For macOS/Linux (python3 must be 3.12)
   python3.12 -m venv venv
   source venv/bin/activate
   
   # For Windows (Python 3.12)
   py -3.12 -m venv venv
   .\venv\Scripts\activate
   
   ```

2. **Install the required packages:**

   ```
   pip install -r requirements.txt
   
   ```

**Option B: Using Conda (Recommended for consistent environments)**

1. **Create and activate a conda environment:**

   ```
   conda create -n uderia312 python=3.12
   conda activate uderia312
   
   ```

2. **Install the required packages:**

   ```
   pip install -r requirements.txt
   
   ```

## Step 3: 🔐 Configure Secret Keys (Security)

> **⚠️ CRITICAL SECURITY STEPS**

The application requires three secrets for secure operation:

### 3a. Create `.env` File with SECRET_KEY (REQUIRED)

The application uses `SECRET_KEY` for session management (cookies, CSRF protection). This **must** be set before starting the application.

```bash
# Generate secure SECRET_KEY and create .env file
python -c "import secrets; key=secrets.token_urlsafe(32); open('.env', 'w').write(f'SECRET_KEY={key}\n'); print(f'✓ Created .env with SECRET_KEY')"
```

Alternatively, manually create `.env` in the project root:
```
SECRET_KEY=your_random_secret_key_here_make_it_long_and_random
```

**Note:** Without this file, the application **will not start** and will show: `ValueError: SECRET_KEY is not set`

### 3b. JWT Secret Key (automatic)

On first start the server generates a unique `tda_keys/jwt_secret.key` (permissions 600), or uses `TDA_JWT_SECRET_KEY` if that variable is set. No key is shipped with the repository. Run the regeneration script only to rotate the key (this signs out every session) or when an image or copy was cloned from another installation:

```bash
python maintenance/regenerate_jwt_secret.py
```

### 3c. Encryption Key for Stored Secrets (before you enter any API key)

`TDA_ENCRYPTION_KEY` encrypts every stored secret: LLM API keys, database connections, connector and SSO credentials, file keys. Without it Uderia uses a public development default (`dev-master-key-change-in-production`), and a hardened deployment refuses to start. Set it in `.env` before the first start, while no secret is stored yet (at least 32 characters):

```bash
python -c "import secrets; open('.env', 'a').write(f'TDA_ENCRYPTION_KEY={secrets.token_urlsafe(48)}\n'); print('✓ Added TDA_ENCRYPTION_KEY to .env')"
```

On an installation that already holds secrets, do not set the key by hand: that makes every stored secret unreadable. Stop the server and run `python maintenance/rotate_encryption_key.py`, which re-encrypts everything under a new key and writes it to `.env`.

## Step 4: Install the Application in Editable Mode

This crucial step links your source code to your Python environment, resolving all import paths. **Run this command from the project's root directory** — the repository already contains its `pyproject.toml` (do not replace it).

```
pip install -e .
```

The `-e` flag stands for "editable," meaning any changes you make to the source code will be immediately effective without needing to reinstall.

## Step 5: Bootstrap Configuration (Optional)

The application uses a **bootstrap configuration system** with `tda_config.json` as a read-only template. This file provides default profiles, MCP servers, and LLM configurations that are copied to each user on their first login.

**Understanding the Bootstrap System:**

- **tda_config.json** - Read-only template containing default configurations
- **First Login** - Configuration is copied from template to user's database record
- **Per-User Storage** - All subsequent changes are stored in the user's database (isolated from other users)
- **Future Users** - New users automatically receive the current template configuration
- **Admin Customization** - Administrators can modify `tda_config.json` to customize defaults for future users

**Default Bootstrap Configuration:**

The template includes:
- **8 Profiles**: one per IFOC class (Ideate `IDEAT`, Optimize `OPTIM`, Focus `FOCUS`, Coordinate `COORD`) on an open model, and the same four on an entry frontier model (`IDGGT`, `OPGGT`, `FOGGT`, `COGGT`)
- **1 MCP Server**: Teradata MCP (edit host/port/path)
- **9 LLM Configurations**: Google, Friendli.AI, OpenAI, Anthropic, Azure, AWS Bedrock, OpenRouter, xAI, Celeris (API keys required)
- Tool and prompt selections vary per profile (about 30 tools on the Ideate and Optimize profiles)

**Customizing the Bootstrap (Optional):**

Before starting the application for the first time, you can customize `tda_config.json` to pre-configure settings for all future users:

1. **Edit `tda_config.json`** in the project root
2. **Modify MCP Servers** - Add your production MCP server connection details
3. **Adjust Profiles** - Change default profiles, tools, or prompts
4. **Set LLM Defaults** - Pre-configure LLM provider settings (API keys should still be entered per-user)

**⚠️ Important Notes:**
- Changes to `tda_config.json` **only affect new users** created after the modification
- Existing users retain their database-stored configuration (not affected by template changes)
- Each user's configuration is **completely isolated** - changes by one user don't affect others
- The application **never modifies** `tda_config.json` - it remains a read-only template

## Step 6: Start the Application

The application uses a **multi-user authentication system** with JWT tokens and encrypted credential storage. Authentication is **always required** for all users.

**Run the application:**

```bash
python -m trusted_data_agent.main
```

The application will:
- Automatically create `tda_auth.db` (SQLite database with encrypted credentials)
- Initialize default admin account: `admin` / `admin` (⚠️ **change immediately!**)
- Start the web server on `http://localhost:5050`

## Step 7: First Login and Security

1. **Open your browser** to `http://localhost:5050`
2. **Login** with default credentials: `admin` / `admin`
3. **⚠️ IMPORTANT:** Immediately change the admin password in the **Administration** panel
4. **Bootstrap Applied** - On first login, your account receives the template configuration from `tda_config.json`
5. **Configure Anatomy** - Complete your setup in the **Anatomy** view:
   - Add API keys for LLM providers
   - Configure MCP server connection details (host, port, path)
   - Enable/disable profiles as needed
6. **Create User Accounts** - Admin users can create additional users (each receives the bootstrap configuration)

**Authentication Features:**
- ✅ JWT tokens (24-hour expiry, configurable) for web UI sessions
- ✅ Long-lived access tokens for REST API automation
- ✅ Per-user credential encryption using Fernet
- ✅ User tiers: `user`, `developer`, `admin`
- ✅ Soft-delete audit trail for revoked tokens
- ✅ Session management with persistent context
- ✅ Bootstrap configuration copied to each user on first login
- ✅ Consumption profile enforcement with granular usage quotas
- ℹ️ Rate limiting is **on** by default (`rate_limit_enabled`); switch it off in **Administration → Config → Security → Rate Limiting** for a single-user install

## Running the Application

**Important:** All commands must be run from the project's **root directory**.

### Standard Mode

For standard operation:

```
python -m trusted_data_agent.main

```

---

## Model Selection: Recommended vs All Models

When adding or editing LLM configurations, you can switch between **Recommended** and **All** models using the toggle in the model selection UI:

- **Recommended (default):** Shows only tested and recommended models enabled for selection. Non-recommended models are visible but disabled.
- **All:** Enables all available models from the provider for selection.

**Note:** Ollama models are community-maintained. When connecting to Ollama, you can use the "All" toggle to select any locally installed model.

[LLM Provider Architecture →](../Architecture/LLM_PROVIDER_ARCHITECTURE.md)

---

## Command Line Options

The Uderia platform supports several command-line options for different deployment scenarios and operational modes:

### Available Options

```bash
python -m trusted_data_agent.main [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--host` | Host address to bind the server to. Use `0.0.0.0` for Docker deployments. | `127.0.0.1` |
| `--port` | Port to bind the server to. | `5050` |
| `--gitcall` | Enable the GitHub API call that fetches the repository star count. | Disabled |
| `--nogitcall` | Disable that call explicitly (the default; kept for existing launch lines, always wins). | — |
| `--offline` | Use cached HuggingFace models only (skip remote version checks). Useful when internet is slow or unavailable. | Disabled |

### Usage Examples

**Standard production deployment:**
```bash
python -m trusted_data_agent.main
```

**Offline mode (use cached models):**
```bash
python -m trusted_data_agent.main --offline
```

**Docker deployment (what `docker-compose.yml` runs):**
```bash
python -m trusted_data_agent.main --host 0.0.0.0 --port 5050 --nogitcall
```

**Combined options:**
```bash
python -m trusted_data_agent.main --offline --nogitcall
```

**With the GitHub star count (public checkouts only):**
```bash
python -m trusted_data_agent.main --gitcall
```

### Offline Mode Details

The `--offline` flag is particularly useful when:
- Internet connection is slow or unreliable
- HuggingFace model downloads are timing out
- Working in air-gapped or restricted network environments
- Models are already cached from previous installations

**Note:** The offline mode requires that HuggingFace models have been previously downloaded to the cache directory (`~/.cache/huggingface/hub/`). First-time installations will need internet access to download required models.
