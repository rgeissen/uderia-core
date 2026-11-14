# Planner Repository Constructors - User Guide

## Overview

The **Planner Repository Constructor** system enables automatic generation of execution strategies and planning patterns through a modular plugin architecture. These constructors build **Planner Repositories** - specialized RAG collections that store proven execution traces, SQL query patterns, and successful agent interactions. Constructors define reusable patterns for specific use cases (SQL queries, API calls, workflows) and support both manual and LLM-assisted population.

**Note:** Planner Repositories are distinct from Knowledge Repositories (general document stores). Planner Repositories contain execution patterns that guide the agent's decision-making, while Knowledge Repositories provide reference documentation accessible during planning.

> **Related Documentation:**
> - `../../rag_templates/TYPE_TAXONOMY.md` - **Comprehensive type system documentation** explaining template_type, repository_type, and category concepts
> - `../../rag_templates/PLUGIN_MANIFEST_SCHEMA.md` - Template plugin manifest schema and field specifications
> - `../../rag_templates/schemas/README.md` - JSON schema validation details for template files

## Key Features

✅ **LLM-Assisted Question Generation** - Automatically generate question/answer pairs from database schemas  
✅ **Modular Plugin System** - Templates are self-contained plugins with manifest files  
✅ **Two-Phase Strategy Templates** - Execute queries and generate natural language reports  
✅ **Multiple Database Support** - Teradata, PostgreSQL, MySQL, and others via MCP tools  
✅ **UI-Based Workflow** - Complete end-to-end workflow in the web interface  
✅ **Template Validation** - Automatic validation of inputs and arguments

## Available Templates

### SQL Query Constructor - Database Context (`sql-query-basic`)

Generate question/SQL pairs from database schema and business requirements using MCP context.

**Features:**
- Uses MCP tools to extract database schema (HELP TABLE, DESCRIBE, etc.)
- LLM generates questions based on business context topic
- Supports 1-1000 question/SQL pairs per generation
- Works with any database supported by MCP tools (Teradata, PostgreSQL, MySQL, etc.)

**Population Mode:**
- **Auto-Generate (LLM)**: Generate from schema context
- **Manual**: Enter question/SQL pairs directly

**Use Cases:**
- Generate training data from production schemas
- Create diverse SQL query examples for specific databases
- Build knowledge bases for business analytics scenarios

### SQL Query Constructor - Document Context (`sql-query-doc-context`)

Generate question/SQL pairs from technical documentation (PDF, TXT, DOC, DOCX) using document upload.

**Features:**
- Upload technical documentation (DBA guides, performance docs, operational manuals)
- Provider-aware document processing (native upload for Google/Anthropic/Amazon, text extraction for others)
- LLM extracts concepts and generates practical SQL questions
- Supports multiple files up to 50MB each
- Maximum 1000 question/SQL pairs per generation

**Population Mode:**
- **Auto-Generate (Document Upload)**: Upload documents and generate questions
- **Manual**: Not supported (use document upload mode)

**Use Cases:**
- Convert DBA documentation into actionable SQL queries
- Extract best practices from performance tuning guides
- Generate troubleshooting queries from operational manuals
- Build knowledge bases from vendor documentation

**Document Upload Configuration:**
Access the Document Upload Configuration in the Admin panel to customize:
- Provider-specific upload methods (native vs. text extraction)
- Maximum file sizes per provider
- Enable/disable upload for specific providers

### Other registered templates

`rag_templates/template_registry.json` currently registers seven templates. Besides the two planner constructors above:

| `template_id` | Plugin directory | Type | Populates |
|---|---|---|---|
| `knowledge_repo_v1` | `knowledge-repository-basic` | `knowledge_repository` | Knowledge repository (document chunks) |
| `kg_database_context_v1` | `kg-database-context` | `knowledge_graph` | Knowledge graph from MCP schema context |
| `kg_industry_model_v1` | `kg-industry-model` | `knowledge_graph` | Knowledge graph from a Databricks industry `model.json` (deterministic, no LLM) |
| `kg_andp_semantic_v1` | `kg-andp-semantic` | `knowledge_graph` | Knowledge graph from a deployed AI-Native Data Product's self-published catalog (deterministic) |
| `kg_declared_model_v1` | `kg-declared-model` | `knowledge_graph` | Knowledge graph from a model that stores ISO/IEC 11179 metadata with itself (deterministic) |

Knowledge-graph templates populate the graph store (see [KNOWLEDGE_GRAPH_ARCHITECTURE.md](../Architecture/KNOWLEDGE_GRAPH_ARCHITECTURE.md)), not a vector collection.

## Quick Start

### 1. Create a Collection

In the TDA web interface:
1. Navigate to **RAG Collections** tab
2. Click **Add RAG Collection**
3. Enter collection name and select MCP server
4. Choose **Populate with SQL Template**

### 2. Generate Questions (LLM-Assisted)

#### Option A: From Database Schema (Business Context Template)

With **SQL Query Constructor - Database Context** selected:

1. **Generate Context** - Execute a sample query to extract database schema
2. **Enter Configuration**:
   - **Context Topic**: e.g., "Employee analytics and reporting"
   - **Num Examples**: 1-1000 (number of question/SQL pairs to generate)
   - **Database Name**: Your target database
3. **Generate Questions** - LLM creates question/SQL pairs based on schema
4. **Review & Edit** - Verify and modify generated examples
5. **Populate Collection** - Add cases to the collection
6. **Create Collection** - Finalize and activate

#### Option B: From Documents (Document Context Template)

With **SQL Query Template - Document Context** selected:

1. **Upload Documents** - Click to upload PDF, TXT, DOC, or DOCX files
   - Supports multiple files
   - Max 50MB per file (varies by provider)
2. **Enter Configuration**:
   - **Context Topic**: e.g., "database performance tuning"
   - **Num Examples**: 1-1000
   - **Database Name**: Your target database
3. **Generate Questions** - LLM analyzes documents and creates question/SQL pairs
4. **Review & Edit** - Verify generated examples
5. **Populate Collection** - Add cases to collection
6. **Create Collection** - Finalize and activate

### 3. Manual Population

Alternatively, use **Manual Input** to enter question/SQL pairs directly:
- Add multiple question/SQL examples
- Specify database name and MCP tool
- Populate collection immediately

## Adding a Custom Template

> **Verified against `agent/rag_template_manager.py` (2026-10-30).** Templates are loaded **only** from entries in `rag_templates/template_registry.json`, and each entry's `plugin_directory` is resolved under `rag_templates/templates/`. There is no automatic registration and no override-by-`template_id`.

### How a template becomes usable

1. Create a plugin directory under `rag_templates/templates/<your-template>/` with `manifest.json` and the template JSON (see [TEMPLATE_PLUGIN_DEVELOPMENT.md](TEMPLATE_PLUGIN_DEVELOPMENT.md) and [COMPLETE_EXAMPLE.md](COMPLETE_EXAMPLE.md)).
2. Add an entry to `rag_templates/template_registry.json` (`template_id`, `plugin_directory`, `template_file`, `manifest_file`, `status`, `display_order`, `category`). Entries with `status: "deprecated"` are skipped; `active`, `beta` and `coming_soon` are loaded.
3. Optionally validate the package: `POST /api/v1/rag/templates/validate` with `{"plugin_path": "/abs/path/to/plugin"}` (returns `is_valid`, `errors`, `warnings`).
4. Hot-reload (no restart): `POST /api/v1/rag/templates/reload`, or **Administration → Templates**.

```bash
JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | jq -r '.token')

curl -X POST http://localhost:5050/api/v1/rag/templates/reload \
  -H "Authorization: Bearer $JWT"
```

**Response:**
```json
{
  "status": "success",
  "message": "Templates reloaded successfully",
  "count": 7,
  "templates": [ ... ]
}
```

Each template is validated on load (JSON schema from `rag_templates/schemas/` when `jsonschema` is installed — `knowledge_repository` uses the knowledge schema, `knowledge_graph` has basic validation only, everything else the planner schema). A template that fails validation is logged and skipped, not loaded.

5. Verify in the UI: **Intelligence → Planner Repositories → Create New Collection**, the template appears in the dropdown.

### `~/.tda/templates/` (not used)

The platform does not scan `~/.tda/templates/` (it was once listed by discovery although nothing there could be loaded). Templates load — and are discovered — only from `rag_templates/templates/`, through entries in `template_registry.json`. To add one, copy it into `rag_templates/templates/` and register it as above.

Because built-in plugin directories live inside the installation, keep custom templates in your own version control and re-apply the directory and registry entry after an upgrade.

### Troubleshooting

- **Template not in the dropdown:** check the registry entry (`plugin_directory` must match the folder name; `template_file` basename must exist in it), validate the JSON (`jq . manifest.json`), reload, and look for `Template file not found` / `Template schema validation failed` in the server log.
- **List what is loaded:** `curl -s http://localhost:5050/api/v1/rag/templates/list -H "Authorization: Bearer $JWT" | jq`.

## Template Structure (Built-In)

### Plugin Directory Layout

```
rag_templates/templates/                # Built-in system templates
├── sql-query-basic/
│   ├── manifest.json                   # Plugin metadata and UI configuration
│   ├── sql_query_v1.json              # Template definition (strategy)
│   └── README.md                       # Documentation
└── sql-query-doc-context/
    ├── manifest.json
    ├── sql_query_doc_context_v1.json
    └── README.md
```

### Manifest.json

Defines plugin metadata, UI fields, and validation rules:

```json
{
  "name": "sql-query-basic",
  "version": "1.0.0",
  "template_id": "sql_query_v1",
  "display_name": "SQL Query Template - Business Context",
  "description": "Two-phase strategy...",
  
  "population_modes": {
    "manual": {
      "enabled": true,
      "input_variables": {
        "database_name": {
          "required": true,
          "description": "Target database name"
        }
      }
    },
    "auto_generate": {
      "enabled": true,
      "input_variables": {
        "context_topic": {
          "required": true,
          "type": "string",
          "description": "Business context for question generation"
        },
        "num_examples": {
          "required": true,
          "type": "integer",
          "default": 5,
          "min": 1,
          "max": 1000,
          "description": "Number of question/SQL pairs to generate"
        },
        "database_name": {
          "required": false,
          "description": "Target database name"
        }
      }
    }
  }
}
```

### Template JSON (Strategy Definition)

Defines the execution strategy with phases and arguments:

```json
{
  "template_id": "sql_query_v1",
  "template_version": "1.0.0",
  "strategy_template": {
    "strategy_name": "SQL Query Strategy",
    "phases": [
      {
        "phase_number": 1,
        "phase_description": "Execute SQL query",
        "tool": "base_readQuery",
        "arguments": [
          {
            "name": "sql",
            "type": "sql_statement",
            "required": true,
            "description": "The SQL query to execute (includes database name)"
          }
        ]
      },
      {
        "phase_number": 2,
        "phase_description": "Generate final report",
        "tool": "TDA_FinalReport",
        "arguments": [
          {
            "name": "user_query",
            "type": "user_query",
            "required": true
          }
        ]
      }
    ]
  }
}
```

## LLM Question Generation

### How It Works

1. **Context Extraction**: Execute a sample query (e.g., `HELP TABLE tablename;`) to get schema information
2. **Schema Analysis**: Extract table structures, columns, data types, and constraints
3. **Prompt Construction**: Build a detailed prompt with:
   - Business context topic
   - Complete schema information
   - Number of examples to generate
   - Target database name
4. **LLM Generation**: Generate diverse, realistic question/SQL pairs
5. **Validation**: Ensure SQL is syntactically valid and questions are meaningful

### Best Practices

- **Context Topic**: Be specific (e.g., "Customer order analysis" vs "general queries")
- **Sample Queries**: Use `HELP TABLE` or `DESCRIBE` to get comprehensive schema
- **Review Generated Cases**: Always review before populating - edit if needed
- **Incremental Generation**: Start with 5-10 examples to test, then scale up

## Template Registry

Templates are registered in `rag_templates/template_registry.json`:

```json
{
  "templates": [
    {
      "template_id": "sql_query_v1",
      "template_file": "sql-query-basic/sql_query_v1.json",
      "plugin_directory": "sql-query-basic",
      "manifest_file": "sql-query-basic/manifest.json",
      "status": "active",
      "display_order": 1,
      "category": "Database",
      "is_builtin": true
    }
  ]
}
```

## API Endpoints

### Get Available Templates
```bash
GET /api/v1/rag/templates
```

### Get Template Plugin Info
```bash
GET /api/v1/rag/templates/{template_id}/plugin-info
```

### Generate Questions from Schema (Business Context)
```bash
POST /api/v1/rag/generate-questions
Content-Type: application/json
Authorization: Bearer YOUR_JWT_TOKEN

{
  "template_id": "sql_query_v1",
  "execution_context": "{...extracted schema...}",
  "subject": "Customer analytics",
  "count": 10,
  "database_name": "sales_db"
}
```

### Generate Questions from Documents (Document Context)
```bash
POST /api/v1/rag/generate-questions-from-documents
Content-Type: multipart/form-data
Authorization: Bearer YOUR_JWT_TOKEN

Form Data:
- subject: "performance tuning"
- count: 10
- database_name: "production_db"
- target_database: "Teradata"
- files: [dba_guide.pdf, optimization_tips.pdf]
```

### Populate Collection
```bash
POST /api/v1/rag/collections/{collection_id}/populate
{
  "template_type": "sql_query",
  "examples": [
    {
      "user_query": "Show all active customers",
      "sql_statement": "SELECT * FROM customers WHERE status = 'active'"
    }
  ],
  "database_name": "sales_db"
}
```

## Troubleshooting

### Template Not Loading
- Check `template_registry.json` for correct `template_id` and file paths
- Verify manifest.json has required fields
- Reload templates (`POST /api/v1/rag/templates/reload`, Administration → Templates) or restart the server

### Validation Errors
- Ensure `num_examples` is within min/max range (1-1000)
- Verify all required fields are provided
- Check that SQL statements include database name in the query string

### Generated Questions Poor Quality
- Refine context topic to be more specific
- Provide more comprehensive schema information in sample query
- Adjust the number of examples (fewer can be higher quality)

## Advanced: Creating Custom Templates

See `TEMPLATE_PLUGIN_DEVELOPMENT.md` for detailed guide on:
- Template plugin structure
- Manifest configuration
- Strategy definition
- UI field customization
- Validation rules
- Distribution and installation

## Migration Notes

### From Legacy System
- Old flat template files (`sql_query_v1.json` at root) are deprecated
- Use plugin directory structure (`templates/plugin-name/`)
- Manifest files now required for UI integration
- Template arguments refined (database_name removed from Phase 1)

## Support

For issues or questions:
- GitHub: https://github.com/rgeissen/uderia-core
- Check logs: `logs/` directory
- Validate templates: Check browser console and server logs
