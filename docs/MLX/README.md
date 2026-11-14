# MLX Integration Guide

MLX is a machine learning framework optimized for Apple Silicon (M-series chips). This guide explains how to set up and use MLX with Uderia for local, on-device LLM inference.

## Overview

**MLX LM Server** runs a local OpenAI-compatible API on your Mac, allowing Uderia to send queries to large language models running entirely on your device—no API keys, no external services, full privacy.

### Key Features

- ✅ **Local Execution** — Models run on your Mac, your data stays on your device
- ✅ **Cost-Free** — No API charges, only local compute costs
- ✅ **Fast** — Optimized for Apple Silicon hardware
- ✅ **OpenAI Compatible** — Works seamlessly with Uderia's LLM interface

### System Requirements

- **Mac with Apple Silicon** — M1, M2, M3, M4, etc.
- **macOS 12+** — Most recent macOS versions
- **RAM** — Depends on model size:
  - 7B models: 16GB+ recommended
  - 13B models: 24GB+ recommended
  - 70B models: 80GB+
- **Disk Space** — 5–40GB for model weights (depends on model and quantization)

## Installation

### 1. Install MLX and MLX-LM

```bash
# Create a virtual environment (recommended)
python3 -m venv mlx_env
source mlx_env/bin/activate

# Install MLX and MLX-LM
pip install mlx mlx-lm
```

**Note:** Ensure you're using Python 3.9+ on Apple Silicon. If you're on an Intel Mac, MLX will not work.

Verify installation:
```bash
python -c "from mlx_lm import models; print('MLX installed successfully')"
```

### 2. Download a Model

MLX hosts quantized models on Hugging Face. Popular options:

| Model | Size | Quantization | Use Case |
|-------|------|--------------|----------|
| `mlx-community/Llama-3-8B-Instruct-4bit` | 8B | 4-bit | General purpose, balanced |
| `mlx-community/Mistral-7B-Instruct-v0.1-4bit` | 7B | 4-bit | Fast, lightweight |
| `mlx-community/Qwen2-7B-Instruct-4bit` | 7B | 4-bit | High quality, multilingual |
| `mlx-community/Llama-2-13b-chat-4bit` | 13B | 4-bit | Larger, more capable |
| `mlx-community/Mistral-Nemo-Instruct-2407-4bit` | 12B | 4-bit | State-of-the-art small model |

Models are automatically downloaded on first use. First run will take a few minutes.

## Starting the MLX Server

### Basic Start

```bash
# Start MLX LM server (defaults to http://localhost:8080)
python -m mlx_lm.server --model mlx-community/Mistral-7B-Instruct-v0.1-4bit
```

Output should show:
```
INFO:     Started server process [12345]
INFO:     Uvicorn running on http://0.0.0.0:8080
INFO:     Application startup complete
```

### Custom Host/Port

```bash
# Use a different port
python -m mlx_lm.server --model mlx-community/Mistral-7B-Instruct-v0.1-4bit --host 0.0.0.0 --port 5000

# Access via: http://localhost:5000
```

### Advanced Options

```bash
# Use GPU acceleration (if available)
python -m mlx_lm.server --model <model-name> --use-gpu

# Set max tokens for responses
python -m mlx_lm.server --model <model-name> --max-tokens 2048

# Run in background (macOS/Linux)
nohup python -m mlx_lm.server --model <model-name> > mlx_server.log 2>&1 &
```

### Docker (Optional)

Run MLX in a Docker container:

```bash
docker run -it --platform linux/arm64 \
  -p 8080:8080 \
  -v ~/.cache:/root/.cache \
  mlx-lm:latest \
  python -m mlx_lm.server --model mlx-community/Mistral-7B-Instruct-v0.1-4bit
```

## Configuring Uderia

### 1. Create MLX LLM Configuration

1. Start Uderia: `python -m trusted_data_agent.main`
2. Navigate to **Anatomy → LLMs**
3. Click **Add Configuration**
4. Fill in:
   - **Provider:** MLX
   - **MLX LM Server URL** (`mlx_host`): `http://localhost:8080` (or your server URL; default `http://localhost:8080`)
   - **Model:** pick from the dropdown — Uderia lists the ids the server reports at `GET <mlx_host>/v1/models` (e.g. `mlx-community/Mistral-7B-Instruct-v0.1-4bit`). The id is sent as the request's `model`, so use the served id rather than a free-form name.
5. Click **Test Connection**

Expected response: `MLX LM server connection successful (model: <model id>).`

No API key is needed. The `MLX_HOST` environment variable is a fallback: it is used only when no stored credentials are found for the MLX configuration (LangChain lanes, `llm/langchain_adapter.py`).

### 2. Create a Profile Using MLX

1. Go to **Anatomy → Agent Profiles**
2. Click **Create Profile**
3. Fill in:
   - **Profile Name:** `Local Mistral` (your choice)
   - **Profile Type:** Choose one:
     - **Ideate** (`llm_only`) — Direct LLM conversation
     - **Focus** (`rag_focused`) — Knowledge retrieval + LLM synthesis
     - **Optimize** (`tool_enabled`) — Full Planner/Executor with tools
     - **Coordinate** (`genie`) — Multi-expert coordination
4. **LLM Configuration:** Select your MLX config
5. **MCP Server:** Select your data source (Teradata, Postgres, etc.)
6. Click **Save**

### 3. Set as Default (Optional)

1. In **Anatomy → Agent Profiles**, find your new MLX profile
2. Click its **Set as Default** button (a profile must pass its tests before it can become the default)

Now all new sessions use this profile by default.

## Testing

### Quick Test via REST API

```bash
# 1. Login
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | jq -r '.token')

# 2. Create session
SESSION=$(curl -s -X POST http://localhost:5050/api/v1/sessions \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{}' | jq -r '.session_id')

# 3. Submit query
TASK=$(curl -s -X POST http://localhost:5050/api/v1/sessions/$SESSION/query \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the capital of France?"}' | jq -r '.task_id')

# 4. Poll until the task finishes (status is "complete", not "completed")
until curl -s -H "Authorization: Bearer $JWT" "http://localhost:5050/api/v1/tasks/$TASK" \
  | jq -e '.status | IN("complete","failed","error")' >/dev/null; do sleep 3; done
curl -s -X GET "http://localhost:5050/api/v1/tasks/$TASK" \
  -H "Authorization: Bearer $JWT" | jq '.result'

# To target the MLX profile without making it the default, add its id to the
# query body: {"prompt": "...", "profile_id": "<mlx-profile-id>"} — never an @TAG in the prompt.
```

### UI Test

1. Open Uderia at `http://localhost:5050`
2. Log in (admin/admin)
3. Type a simple question: "Hello, what's 2+2?"
4. Watch the response stream live

## Model Selection Guide

### For Different Use Cases

**Chat & General Q&A:**
- `mlx-community/Mistral-7B-Instruct-v0.1-4bit` — Fast, good quality
- `mlx-community/Llama-3-8B-Instruct-4bit` — Balanced

**Data Analysis & SQL:**
- `mlx-community/Qwen2-7B-Instruct-4bit` — Strong at code/SQL
- `mlx-community/Llama-3-8B-Instruct-4bit` — Good for structured output

**Long Context:**
- `mlx-community/Mistral-Nemo-Instruct-2407-4bit` — 128K context window
- `mlx-community/Llama-2-13b-chat-4bit` — More capable

**Smallest/Fastest:**
- `mlx-community/Mistral-7B-Instruct-v0.1-4bit` — Only 7B params
- `mlx-community/phi-2-4bit` — Ultra-lightweight

### Memory Requirements

| Model Size | 4-bit Quantization | Recommended RAM |
|------------|-------------------|-----------------|
| 7B | ~4GB | 16GB |
| 13B | ~8GB | 24GB |
| 30B | ~18GB | 32GB |
| 70B | ~42GB | 64GB+ |

**Tip:** Start with a 7B model (Mistral or Phi) to test. Upgrade to 13B once comfortable.

## Troubleshooting

### "MLX LM server at 'http://localhost:8080' is not reachable"

**Cause:** MLX server not running or wrong URL configured.

**Fix:**
1. Verify server is running: `curl http://localhost:8080/v1/models`
2. Check port matches (default 8080)
3. If using a different machine, ensure firewall allows access
4. Restart both server and Uderia

### Server Crashes / Out of Memory

**Cause:** Model too large for your RAM.

**Fix:**
1. Stop MLX server
2. Switch to a smaller model:
   ```bash
   python -m mlx_lm.server --model mlx-community/Mistral-7B-Instruct-v0.1-4bit
   ```
3. Monitor memory: `Activity Monitor → Memory`
4. If still crashing, reduce `max-tokens` or use a quantized model

### Slow Responses

**Cause:** Model too large, or CPU throttling.

**Fix:**
1. Use a smaller model (7B instead of 13B)
2. Ensure no other heavy processes running
3. Close unnecessary applications
4. Check thermal throttling: `sysctl hw.thermal.mode`

### Model Download Fails

**Cause:** Network issue or disk space.

**Fix:**
```bash
# Clear cache and retry
rm -rf ~/.cache/huggingface/hub/models--mlx-community*
python -m mlx_lm.server --model mlx-community/Mistral-7B-Instruct-v0.1-4bit
```

## Environment Variables

Configure MLX behavior via environment variables:

```bash
# MLX server host (for Uderia to connect)
export MLX_HOST=http://localhost:8080

# Hugging Face token (if using private models)
export HF_TOKEN=hf_xxxxxxxxxxxx

# Disable GPU (force CPU-only)
export DISABLE_GPU=1

# Debug logging
export MLX_VERBOSE=1
```

Then restart Uderia:
```bash
python -m trusted_data_agent.main
```

## Performance Tips

### 1. Pre-warm the Model

Run a dummy query before production use:
```bash
curl -X POST http://localhost:8080/v1/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "mistral-7b", "prompt": "test", "max_tokens": 1}'
```

### 2. Keep Long-Running Sessions

Sessions maintain conversation history, so second+ turns are faster:
- Turn 1: ~2–5s (includes model loading)
- Turn 2+: ~1–3s (model cached)

### 3. Monitor Resource Usage

```bash
# Terminal 1: Watch memory/CPU
top -l 9999 -s 1 | grep -E "MLXLLM|Uderia|MEM"

# Terminal 2: Run queries in Uderia
```

### 4. Batch Queries

Group related queries in a single session to reuse model cache.

## Advanced Usage

### Custom Models

MLX models are on Hugging Face. To use a custom model:

```bash
# Fine-tuned or community model
python -m mlx_lm.server --model huggingface-account/my-custom-model
```

See [MLX Model Zoo](https://huggingface.co/mlx-community) for available models.

### Integration with Agent Packs

Use MLX in custom agent packs:

```json
{
  "llm_configurations": [
    {
      "provider": "MLX",
      "model": "Mistral-7B",
      "mlx_host": "http://localhost:8080"
    }
  ]
}
```

## Limitations

- **No API key** — Can't use cloud-based model updates or commercial support
- **Hardware-bound** — Only works on Apple Silicon Macs
- **Model size** — Limited by available RAM
- **Token limits** — Slower than commercial APIs (but free and private)
- **No fine-tuning UI** — Fine-tuning requires manual setup

## Resources

- [MLX Official Docs](https://ml-explore.github.io/mlx/)
- [MLX-LM GitHub](https://github.com/ml-explore/mlx-llm)
- [MLX Model Zoo](https://huggingface.co/mlx-community)
- [Apple Metal Performance Shaders](https://developer.apple.com/metal/)

## FAQ

**Q: Can I use MLX on Intel Mac?**  
A: No, MLX requires Apple Silicon. Consider Ollama as an alternative.

**Q: Will my data be sent to Apple/Hugging Face?**  
A: No. Models run locally on your device. Only the initial download uses the network.

**Q: Can I use multiple models simultaneously?**  
A: Yes, run separate MLX servers on different ports and create profiles for each.

**Q: What's the difference between MLX and Ollama?**  
A: MLX is Apple Silicon-native and faster. Ollama supports more architectures but is slower on Macs.

**Q: Can I switch between cloud and local models?**  
A: Yes, create multiple profiles—one with MLX (local) and one with OpenAI/Claude (cloud). Switch profiles in the UI or via query override.

---

**Need help?** Check the [Uderia documentation](../RestAPI/restAPI.md) or open an issue on GitHub.
