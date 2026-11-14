# ⚡ Extensions: Post-Processing Transformations

Extensions transform LLM answers into structured, machine-parseable output for downstream automation. While Skills inject context **before** the query, Extensions run **after** the answer is received — converting non-deterministic LLM output into deterministic formats for workflow tools like n8n, Airflow, and Flowise.

```
User Query → LLM Answer → !Extension Post-Processing → Structured Output → n8n / Airflow / API
```

**How it works:** Type `!` in the chat input to trigger autocomplete, select an extension, and optionally add a parameter (e.g., `!decision:critical`). Multiple extensions can be chained in a single query — they execute serially, each receiving results from prior extensions.

**Built-in extensions:**

| Extension | Purpose | LLM Cost | Output |
|-----------|---------|----------|--------|
| `!json` | Wraps answer + metadata into standardized JSON for APIs | No | Chat |
| `!decision` | Semantic analysis for workflow branching (binary or severity-based) | Yes | Silent |
| `!extract` | Regex-based extraction of numbers, percentages, entities | No | Silent |
| `!classify` | Semantic categorization (alert, performance, data quality, security) | Yes | Silent |
| `!summary` | Executive summary with key points and action items | Yes | Chat |
| `!pdf` | Downloadable PDF export with Markdown-aware formatting | No | Chat |
| `!condition` | Deterministic true/false test (threshold, contains=, /regex/) for flow branches | No | Silent |
| `!format` | Render a template from the answer and its extracted values | No | Silent |
| `!boolean_check` | Judge the answer True / False / Not applicable | Yes | Silent |

**Key characteristics:**
- **Deterministic output** — transforms natural-language answers into structured formats that workflow tools can reliably parse and branch on
- **Four-tier extension framework** — from zero-friction Convention (drop a `.py` file) through Simple and Standard tiers to LLM-powered extensions with automatic token tracking
- **Serial chaining** — compose extensions (`!extract !decision:critical`) where each extension accesses prior results for progressive data refinement
- **Isolated error handling** — extension failures never break the main LLM answer; each extension succeeds or fails independently
- **Create your own** — via the UI scaffold (generates tier-appropriate Python boilerplate with real-time validation), REST API, or manual file drop to `~/.tda/extensions/`
- **Portable** — export/import as `.extension` zip for sharing across environments
- **Admin-governed** — administrators control whether users can create custom extensions
- **Automatic cost tracking** — LLM-powered extensions (Tier 3) automatically track input/output tokens and cost, integrated into session totals

**REST API example:**
```json
{
  "prompt": "What is the CPU usage?",
  "extensions": [
    {"name": "decision", "param": "critical"}
  ]
}
```

The `!decision` extension produces `{result, severity, branch_key}` — exactly what n8n Switch nodes need for deterministic routing (e.g., `threshold_exceeded_critical` → PagerDuty, `nominal_ok` → log only).

Architecture details: [**Extension Architecture (docs/Architecture/EXTENSION_ARCHITECTURE.md)**](../Architecture/EXTENSION_ARCHITECTURE.md)
