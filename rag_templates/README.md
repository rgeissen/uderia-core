# Planner Repository Constructor Library

This directory contains the constructor library for building **Planner Repositories** - specialized RAG collections that store execution strategies and planning patterns. These constructors enable automatic generation of proven execution traces that guide the agent's decision-making process.

**Repository Types:**
- **Planner Repositories** (built by these constructors): Execution patterns and strategies for proven task completion
  - Few-shot learning examples for RAG-based planning
  - Automatically captured from successful agent executions
  - Manually populated via constructor templates
  - Retrieved during `_generate_meta_plan()` for strategic guidance
  
- **Knowledge Repositories** (separate system): General documents and reference materials
  - Domain knowledge, technical documentation, and business context
  - Uploaded as PDF, TXT, DOCX, MD files with configurable chunking
  - Retrieved during `_retrieve_knowledge_for_planning()` for contextual enrichment
  - Fully integrated with planner (Phase 1 complete - Nov 2025)

## Structure

```
rag_templates/
├── README.md                      # This file
├── PLUGIN_MANIFEST_SCHEMA.md      # manifest.json reference
├── TYPE_TAXONOMY.md               # template_type / repository_type / category
├── template_registry.json         # Template registry (the ONLY load list)
├── schemas/                       # JSON schemas (planner, knowledge, manifest)
└── templates/                     # One plugin directory per template
    ├── sql-query-basic/           # sql_query_v1 — SQL Query Constructor, Database Context
    ├── sql-query-doc-context/     # sql_query_doc_context_v1 — Document Context
    ├── knowledge-repository-basic/ # knowledge_repo_v1
    ├── kg-database-context/       # kg_database_context_v1 (knowledge graph)
    ├── kg-industry-model/         # kg_industry_model_v1 (knowledge graph)
    ├── kg-andp-semantic/          # kg_andp_semantic_v1 (knowledge graph)
    └── kg-declared-model/         # kg_declared_model_v1 (knowledge graph)
```

Each plugin directory holds `manifest.json` + the template JSON (+ optional README).

## Template Registry

The `template_registry.json` file contains metadata about all available templates:

- **template_id**: Unique identifier for the template
- **plugin_directory**: Folder under `templates/` that holds the plugin
- **template_file** / **manifest_file**: Paths of the template JSON and manifest (the template file's basename is resolved inside `plugin_directory`)
- **status**: Template status (`active`, `beta`, `coming_soon`, `deprecated`)
- **display_order**, **category**, **is_builtin**: UI ordering and grouping

Every entry except `status: "deprecated"` is loaded at startup (status is carried onto the loaded template).

## Template Definition Format

Each template JSON file contains:

### Required Sections

1. **Metadata**
   - `template_id`: Unique identifier
   - `template_name`: Display name
   - `template_type`: Type identifier (sql_query, api_request, etc.)
   - `description`: Brief description
   - `status`: Template status
   - `version`: Template version

2. **Input Variables**
   - User-provided parameters (e.g., user_query, sql_statement)
   - Each variable specifies: type, required, description, placeholder, validation rules
   
3. **Output Configuration**
   - Auto-generated values (session_id, tokens, feedback, etc.)
   - Marks which values are editable at runtime
   
4. **Strategy Template**
   - Phase definitions and goal templates
   - Tool mappings and argument configurations
   
5. **Metadata Mapping**
   - How input variables map to case metadata
   
6. **Validation Rules**
   - Input validation logic

## Template Usage

Templates are loaded automatically at application startup:

1. **Template Manager** loads `template_registry.json`
2. Loads each non-deprecated entry's template JSON (and `manifest.json`) from its plugin directory under `templates/`
3. Validates template structure (JSON schema from `schemas/` when `jsonschema` is installed; a template that fails is logged and skipped)
4. Makes templates available via `get_template_manager()`

### Accessing Templates

```python
from trusted_data_agent.agent.rag_template_manager import get_template_manager

# Get template manager
manager = get_template_manager()

# List all templates
templates = manager.list_templates()

# Get specific template
sql_template = manager.get_template("sql_query_v1")

# Get editable configuration
config = manager.get_template_config("sql_query_v1")

# Update configuration (runtime only)
manager.update_template_config("sql_query_v1", {
    "default_mcp_tool": "custom_sql_executor",
    "estimated_input_tokens": 200,
    "estimated_output_tokens": 250
})
```

### Creating New Templates

#### Adding a Template

1. Create a plugin directory `templates/<your-template>/` with `manifest.json` and the template JSON (follow `templates/sql-query-basic/`)
2. Add an entry to `template_registry.json` (see fields above)
3. Optionally validate: `POST /api/v1/rag/templates/validate` with `{"plugin_path": "..."}`
4. Hot-reload: `POST /api/v1/rag/templates/reload` (or Administration → Templates) — no restart needed
5. Verify: Intelligence → Planner Repositories → template dropdown

```bash
curl -X POST http://localhost:5050/api/v1/rag/templates/reload \
  -H "Authorization: Bearer $JWT"
```

#### `~/.tda/templates/` — not used

The platform does not scan `~/.tda/templates/`. Loading and discovery are driven solely by `template_registry.json` entries resolved under `templates/` — copy a plugin into `templates/` and register it to use it.

## Runtime Configuration

Template configurations can be edited at runtime via the UI:

- Editable values are marked with `"editable": true` in the template
- Changes are stored in memory during the session
- To persist changes permanently, modify the template JSON file

## Best Practices

1. **Version Templates**: Use semantic versioning (v1.0.0, v1.1.0, v2.0.0)
2. **Maintain Backwards Compatibility**: Create new versions rather than breaking existing templates
3. **Validate Thoroughly**: Ensure all required fields are present
4. **Document Examples**: Include usage examples in the template
5. **Test Before Activating**: Use `"status": "beta"` for testing, then promote to `"active"`

## Example: SQL Query Constructor - Database Context

See `templates/sql-query-basic/sql_query_v1.json` for a complete example of a production-ready template.

Key features:
- Clear input variable definitions with validation
- Editable output configuration
- Dynamic phase goal generation
- Conditional argument inclusion
- Token estimation defaults
