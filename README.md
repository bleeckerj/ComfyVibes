# ComfyVibes

**ComfyUI automation for creative technologists.**


ComfyVibes turns ComfyUI workflows into reusable, automation‑ready building blocks. It extracts workflows from artifacts, organizes them with metadata and param specs, and runs them through ComfyUI with live monitoring — all exposed as clean MCP tools for agentic systems and IDE integrations.

![ComfyVibes terminal demo](docs/assets/terminal_demo.gif)

## Why ComfyVibes

- **Make workflows portable**: Extract from images or video artifacts and store them as structured, reusable assets.
- **Automate with confidence**: MCP‑native tool surface designed for LLMs, agents, and IDEs.
- **Run + observe**: Queue executions, track progress, and collect results programmatically.
- **Stay consistent**: Normalize formats, validate parameters, and preserve metadata.

## What You Can Do

- Extract workflows from ComfyUI artifacts.
- Import and catalog workflows with metadata.
- Patch parameters safely and run workflows on demand.
- Query ComfyUI for nodes, models, and embeddings.

---

## Developer Guide

### MCP Tool Surface

#### ComfyUI Read Tools

- `comfy.nodes.list`
- `comfy.queue.get`
- `comfy.history.get`
- `comfy.models.list`
- `comfy.models.get`
- `comfy.embeddings.list`

#### Workflow Tools

- `workflows.list`
- `workflows.get`
- `workflows.params.get`
- `workflows.save`
- `workflows.delete`
- `workflows.run`
- `workflows.wait`
- `workflows.extract_from_artifact`
- `workflows.import_from_artifact`

### Quick Start

#### Install

```bash
pip install -e .
```

#### Run the MCP Server

```bash
python -m comfy_mcp.mcp_server.cli
```

Or, after `pip install -e .`, use the CLI entry:

```bash
comfy-vibes
```

#### Extract a Workflow

```bash
python -m comfy_mcp.extraction.adapter extract_and_write ./artifact.png
```

#### Run a Stored Workflow

```python
from comfy_mcp.comfy_client import ComfyClient

client = ComfyClient(base_url="http://127.0.0.1:8188")
result = client.queue_prompt(
    workflow_file="./workflows/nebula_api.json",
    params={"prompt": "cosmic dusk", "width": 1024, "height": 768},
)
print(result)
```

### Roadmap

- Claude Code integration
- Codex integration
- VS Code integration
- Cursor workflows
- Notion pipelines
- Astro site kits
- Cloudflare deploy targets

### GitHub Pages

A marketing-forward landing page lives in `docs/`. Publish it via GitHub Pages (root `/docs`), or deploy with GitHub Actions using the workflow in `.github/workflows/pages.yml`.

## License

MIT
