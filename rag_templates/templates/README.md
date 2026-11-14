# Example Template Plugin Structure

This directory contains example template plugins demonstrating the modular plugin architecture.

## Current Examples

### sql-query-basic

The **SQL Query Constructor - Database Context** - a complete, production-ready example showing:

✅ **Plugin Directory Structure:**
```
sql-query-basic/
├── manifest.json              # Plugin metadata
├── sql_query_v1.json         # Template definition
├── README.md                 # Complete documentation
└── LICENSE                   # MIT License
```

✅ **Features Demonstrated:**
- Complete manifest with all metadata
- Multi-database SQL support
- LLM-assisted question generation
- Runtime configuration
- Comprehensive documentation
- Proper licensing

✅ **Use as Reference:**
Copy this structure when creating new templates:
```bash
cp -r sql-query-basic my-new-template
# Edit files to match your template
```

## Creating Your Own

See `docs/TEMPLATE_PLUGIN_DEVELOPMENT.md` for complete guide.

### Quick Start

```bash
# 1. Copy example
cp -r sql-query-basic my-custom-template
cd my-custom-template

# 2. Update manifest.json
# - Change name, template_id, description
# - Update author and repository
# - Modify keywords and metadata

# 3. Update template JSON
# - Define input_variables
# - Configure strategy_template
# - Set validation rules

# 4. Update README.md
# - Document your template
# - Provide usage examples
# - Include troubleshooting

# 5. Validate
curl -X POST http://localhost:5050/api/v1/rag/templates/validate \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"plugin_path": "/path/to/my-custom-template"}'

# 6. Install — place it under rag_templates/templates/ and add an entry to
#    rag_templates/template_registry.json (the loader reads only the registry;
#    ~/.tda/templates/ is listed by /v1/rag/templates/discover but not loaded)
cp -r my-custom-template rag_templates/templates/

# 7. Reload (no restart needed)
curl -X POST http://localhost:5050/api/v1/rag/templates/reload \
  -H "Authorization: Bearer $JWT"
```

## Best Practices

From the sql-query-basic example:

1. **📋 Complete Manifest** - Include all optional fields
2. **📝 Detailed README** - Examples, troubleshooting, versioning
3. **⚖️ Clear License** - Use OSI-approved license
4. **🏷️ Rich Metadata** - Categories, tags, difficulty level
5. **✅ Validation** - Define clear validation rules
6. **📊 Token Estimates** - Help users understand costs
7. **🔒 Minimal Permissions** - Only request what you need
8. **📚 Documentation** - Usage examples for every feature

## Template Categories

Organize your templates by category:

- **Database** - SQL queries, database operations (sql-query-basic)
- **API** - REST API calls, webhooks (coming soon)
- **File Processing** - File uploads, transformations (coming soon)
- **Custom Workflow** - Multi-step processes (coming soon)
- **Data Analysis** - Analytics, reporting (coming soon)

## Future Examples

Coming soon:
- `api-rest-basic/` - REST API request template
- `file-csv-processor/` - CSV file processing
- `data-analytics/` - Data analysis workflow
- `custom-multi-step/` - Complex multi-phase strategy
