# ComfyVibes

![ComfyVibes hero](https://imagedelivery.net/gaLGizR3kCgx5yRLtiRIOw/c1d0dbb0-fe56-41c5-938a-e45bc10c1400/w=900?format=webp)

**The creative engine of ComfyUI workflows without the complexity. Conversational UI. Automation. AI. Pure joy.**

ComfyVibes is for creative technologists, visual storytellers, and anyone who wants to push ComfyUI beyond the ordinary. This toolkit lets you explore the remarkable capabilities of ComfyUI without being boxed in by cryptic node graphs or technical hurdles. Have conversations with your workflows, experiment with generative visual storytelling, and discover new creative directions—no manual wrangling required. ComfyVibes is your bridge to a more intuitive, playful, and generative engagement with ComfyUI, where imagination leads and the platform follows.

---

ComfyVibes is your all-in-one toolkit for extracting, managing, running, and automating ComfyUI workflows—built for creative technologists, artists, and anyone who wants to do more with less friction. Powered by the Model Context Protocol (MCP), ComfyVibes transforms static workflows into living, breathing, automation-ready assets. Integrate with LLMs, orchestrate agentic pipelines, and say goodbye to tedious node wrangling forever.

---

**ComfyUI automation for creative technologists.**


ComfyVibes turns ComfyUI workflows into reusable, automation‑ready building blocks. It extracts workflows from artifacts, organizes them with metadata and param specs, and runs them through ComfyUI with live monitoring — all exposed as clean MCP tools for conversational engagement, agentic systems and, for you alpha geeks, IDE integrations.

<!-- ![ComfyVibes terminal demo](docs/assets/terminal_demo.gif) -->

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

## Workflow Metadata & Search

Each workflow can include a `meta.json` alongside `workflow.json` inside the workflow directory.
This lets you describe workflows in freeform language, add tags, and provide hints for LLM
selection. Example:

```json
{
  "id": "image_edit",
  "name": "Image edit - clothing swap",
  "description": "Edits a product photo to replace the model's clothing item while preserving pose and lighting.",
  "tags": ["image-edit", "clothing", "product"],
  "requires": {"inputs": ["image"], "params": ["positive_prompt"]}
}
```

Use the `workflows_search` tool to find workflows by id, name, description, or tags.

Generate stub metadata files for any workflows missing `meta.json`:

```bash
python scripts/generate_workflow_meta.py
```

---

## Developer Guide

See the [full MCP Tool Surface](#mcp-tool-surface) below for a comprehensive listing of all 65+ tools.

# ComfyVibes




## Why You May Or May Not Love ComfyVibes

- **✨ AI-Powered Automation**: Seamlessly connect your LLMs and let them bash around with your  workflows.
- **🚀 Effortless Extraction**: Instantly pull workflows from images, videos, or JSON so you can find what and how Comfy did what it did.
- **🎨 Creative Freedom**: Designed for fluid, intuitive, and playful exploration—no more rigid, technical UIs.
- **🔁 Save, Reuse, Remix**: Archive your best workflows as standalone JSON, ready to reload, remix, or share.
- **🌐 Broad Format Support**: Works with PNG, WebP, JPEG, MP4, and more so if ComfyUI made it, ComfyVibes can read it.
- **🧩 Plug-and-Play**: No ComfyUI runtime required. Use it anywhere, integrate it everywhere.

---

## Features

- 🤖 **MCP Wrapper for Automation**: Automate ComfyUI workflows with LLMs for next-level creative flexibility.
- 📸 **Extract Workflow Data**: Effortlessly extract from ComfyUI-generated media files.
- 🎬 **Run Workflows**: Execute workflows directly from extracted or saved data—iterate at the speed of thought.
- 💾 **Save and Reuse**: Archive workflows as JSON and bring them back to life anytime.
- 📊 **Workflow Intelligence**: Get deep insights into nodes, connections, and parameters.
- 🔧 **Standalone Operation**: No ComfyUI runtime dependencies—just pure workflow magic.

---


## Get Started in Seconds (Or More! Depending..these are computers and they can be temperamental)

### Installation

```bash
git clone https://github.com/bleeckerj/nfl-comfymcp.git
cd nfl-comfymcp
pip install -e .
```

### Run the MCP Server

```bash
python -m comfy_mcp.mcp_server.cli
```

### Run the MCP HTTP Proxy

The HTTP proxy exposes the same MCP tool surface over JSON HTTP endpoints.

```bash
python -m comfy_mcp.mcp_server.http_server
```

Defaults:

- `COMFY_MCP_HTTP_BIND_HOST=127.0.0.1`
- `COMFY_MCP_HTTP_BIND_PORT=8181`
- `COMFY_MCP_WORKFLOW_LIBRARY_ROOT=./workflows` (via repo launcher scripts)
- `COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS=0` (set to `1` to merge additional roots like `~/.comfy-mcp/workflows`)

You can persist settings in `.env` (see [.env.example](.env.example)).

### Remote ComfyUI and Manager integrations

ComfyMCP keeps the MCP server and workflow library on the laptop while using the configured ComfyUI host as the remote execution target:

```dotenv
COMFY_MCP_COMFY_BASE_URL=http://gpu-host-or-tunnel:8188
COMFY_MCP_HTTP_BIND_HOST=127.0.0.1
COMFY_MCP_HTTP_BIND_PORT=8181
# COMFY_MCP_API_TOKEN=keep-this-server-side
# COMFY_MCP_READONLY_MODE=true
```

Use a private network, VPN, or tunnel between the laptop and GPU host. Keep both the ComfyUI target and the MCP HTTP proxy away from untrusted networks. Manager discovery uses the same `COMFY_MCP_COMFY_BASE_URL`; it returns an unavailable result when Manager v4 is absent.

Capability, job, template, subgraph, asset, tag, and node-catalog tools are read-only. Queue controls, memory release, history deletion, settings writes, uploads, and Manager operations require readonly mode to be disabled, `confirm=true`, and the configured API token when one is set. HTTP callers may provide that token with `Authorization: Bearer ...` or `X-MCP-Token`; tokens are not returned by tools.

Optional remote endpoints are reported as unsupported or unreachable within `comfy_capabilities_get`. The node catalog and capability audit cache for 60 seconds; Manager discovery caches for 300 seconds. Use the refresh arguments after remote ComfyUI or Manager changes.

For controlled live verification, use `COMFY_MCP_LIVE_TESTS=1` for read-only smoke tests. Keep workflow execution and remote mutations behind the separate `COMFY_MCP_LIVE_MUTATION_TESTS=1` gate.

#### Comfy Org auth for API nodes

Some ComfyUI nodes request Comfy Org credentials through hidden inputs. ComfyMCP can supply those values during `workflows_run`, `workflows_run_from_source`, and `workflows_run_aspect_ratio_adjustment` without putting the secret in tool arguments or workflow JSON.

Configure one or both credentials in `.env`:

```dotenv
COMFY_MCP_COMFY_ORG_AUTH_TOKEN_FILE=~/.config/comfy-mcp/comfy-org-auth-token
COMFY_MCP_COMFY_ORG_API_KEY_FILE=~/.config/comfy-mcp/comfy-org-api-key
```

Direct env values also work:

```dotenv
COMFY_MCP_COMFY_ORG_AUTH_TOKEN=your-comfy-org-auth-token
COMFY_MCP_COMFY_ORG_API_KEY=your-comfy-org-api-key
```

File values are preferred for local development because they keep credentials out of shell history and copied config snippets. When both a direct value and file path are set for the same credential, the direct value wins.

At prompt submission time, ComfyMCP sends the configured values to ComfyUI as `extra_data.auth_token_comfy_org` and `extra_data.api_key_comfy_org`. Current ComfyUI treats those keys as sensitive hidden extra data for nodes that declare `AUTH_TOKEN_COMFY_ORG` or `API_KEY_COMFY_ORG`.

If you've installed the package (for example, `pip install -e .`), you can also use:

```bash
comfy-mcp-http
```

Example:

```bash
COMFY_MCP_HTTP_BIND_PORT=8081 python -m comfy_mcp.mcp_server.http_server
```

### HTTP Proxy Smoke Test

With both HTTP proxies running, you can verify connectivity using the built-in client:

```bash
python scripts/http_proxy_client.py --sample
```

Call a specific tool:

```bash
python scripts/http_proxy_client.py --server comfy --tool comfy_queue_get
python scripts/http_proxy_client.py --server photarium --tool photarium_list --args '{"limit": 1}'
```

### MCP TUI / Deterministic Runner

The terminal operator UI and deterministic MCP runner now live in the neutral repo at `/Users/julian/Code/nfl-mcp-tui`. This repository keeps the ComfyMCP server, workflow store, workspace server, and MCP tools.

From the extracted client repo:

```bash
cd /Users/julian/Code/nfl-mcp-tui
nfl-mcp-tui --config mcp_chat_config.json
nfl-mcp-run --config mcp_chat_config.json doctor
```

Launcher equivalents in that repo:

```bash
./run_mcp_chat.sh
./run_mcp_chat_mouse.sh
./run_mcp_tools.sh doctor
```

The default `nfl` profile preserves the current Digester, ComfyMCP, Photarium, Backoffice, Editorial, and Workspace routing behavior. Use the `generic` profile for neutral MCP routing.

### Run A Workflow From An Existing Image

Use `workflows_run_from_source` when you want to start from an image itself instead of naming a workflow up front. The tool can resolve a Photarium image id, URL, or local file path, recover the embedded workflow or reuse a cached lineage package, and then either run immediately or return `needs_input` with a resumable token for missing image bindings.

Docs:

- [Workflow Run From Source / Lineage Cache](docs/workflows_run_from_source.README.md)
- [MCP TUI extraction note](docs/TUI_README.md)

Deterministic tool-runner example:

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_run_from_source \
  --args '{
    "source":"75e92a7e-2838-45a7-6f2c-32a5fde6c300",
    "source_kind":"photarium_id",
    "namespace":"cf-default",
    "overrides":{"denoise":0.7}
  }'
```

TUI examples:

- `Run the workflow embedded in Photarium image 75e92a7e-2838-45a7-6f2c-32a5fde6c300.`
- `Run the workflow from /tmp/ComfyUI_01065.png and keep the same prompt but change denoise to 0.65.`
- `Rerun the workflow that made image b287f5ef-2901-4e27-f6b4-b483fc4a7e00.`

### Extract a Workflow from an Artifact

```bash
python -m comfy_mcp.extraction.adapter extract_and_write ./artifact.png
```

### Run a Workflow (Python)

```python
from comfy_mcp.comfy_client import ComfyClient

client = ComfyClient(base_url="http://127.0.0.1:8188")
result = client.queue_prompt(
    workflow_file="./workflows/nebula_api.json",
    params={"prompt": "cosmic dusk", "width": 1024, "height": 768},
)
print(result)
```

---

## Which Models Work Well?

- I've found that GPT models are pretty moronic when it comes to operating the MCP tooling. This may very well be that I am entirely new to MCP tooling, and there are better ways of articulating the system prompt and such.

- That said, when I used Opus 4.6 for a (fairly expensive expedition) it performed not only better, but efffectively and unfrustratingly, whereas GPT models felt confused and would avoid using the MCP toolling preferring to hit the Comfy API surface directly, which is besides the point of ComfyVibes.

---

## What Can You Do with ComfyVibes?

- Extract and catalog workflows from any ComfyUI artifact
- Automate workflow execution with LLMs and agents
- Patch parameters and run workflows on demand
- Query ComfyUI for nodes, models, and embeddings
- Build creative pipelines, IDE integrations, and more

---

## Workflow Packaging

When you import or collect many workflows, use the packaging script to normalize
`meta.json` + `params.json` and add human-guidance metadata for capability search.

1) Generate an editable hints template:

```bash
/Users/julian/Code/nfl-comfymcp/.venv/bin/python scripts/package_workflows.py \
  --root workflows \
  --write-hints-template workflows_hints.json
```

2) Edit `workflows_hints.json` with fields like:
- `name`, `description`, `tags`
- `use_cases`, `strengths`, `tradeoffs`
- `io_contract`, `examples`, `notes`

3) Apply packaging with hints:

```bash
/Users/julian/Code/nfl-comfymcp/.venv/bin/python scripts/package_workflows.py \
  --root workflows \
  --hints workflows_hints.json
```

This will infer tunable params from workflow nodes, sync `requires.params`, and
persist capability metadata used by `workflows_capabilities_list/get`.

---

## MCP Tool Surface


ComfyVibes exposes a single MCP server — **ComfyMCP** — for ComfyUI interaction and workflow management. All tools listed below are implemented in this repository.

---


### ComfyMCP Tools


#### ComfyUI Read Tools

| Tool | Description |
|------|-------------|
| `comfy_nodes_list` | Return the full ComfyUI node catalog — every available node type and its parameters. |
| `comfy_queue_get` | Return the current ComfyUI queue state, including pending and running prompts. |
| `comfy_server_info` | Return Comfy target diagnostics (configured URL, resolved IPs, and a queue probe result). |
| `comfy_history_get` | Return ComfyUI history data. Optionally filter by `prompt_id` to get results for a specific run. |
| `comfy_models_list` | List all available model folders (checkpoints, loras, vae, embeddings, etc.). |
| `comfy_models_get` | List available model files within a specific folder (e.g., all checkpoint files). |
| `comfy_embeddings_list` | Return all available embedding names for use in prompts. |
| `list_tools` | Return tool definitions for this MCP server (self-documenting). |


#### Workflow Management Tools

| Tool | Description |
|------|-------------|
| `workflows_list` | List workflows with metadata plus provenance fields (`source_root`, `source_path`, `is_primary_root`) and `params_count`. |
| `workflows_get` | Return the full workflow JSON and metadata for a given `workflow_id`. |
| `workflows_params_get` | Return the `params.json` for a workflow — the tunable parameter spec. |
| `workflows_package` | Regenerate `params.json` + capability metadata for a workflow, with optional human hints. |
| `workflows_package_many` | Batch-package many/all workflows into MCP-ready metadata and params. |
| `workflows_package_template_get` | Return an editable hints template (`use_cases`, `examples`, `io_contract`, etc.). |
| `workflows_file_read` | Read a text file under the primary workflows directory. |
| `workflows_file_copy` | Copy a file under the primary workflows directory. |
| `workflows_save` | Save a workflow entry and always regenerate packaged `meta.json` + `params.json` (optional metadata hints + auth token). |
| `workflows_delete` | Delete a workflow entry from the store by `workflow_id`. |


#### Workflow Execution Tools

| Tool | Description |
|------|-------------|
| `workflows_run` | Run a workflow with parameter overrides via ComfyUI. Supports `force` to bypass caching, `client_id` for tracking, and configured Comfy Org hidden auth for API nodes. |
| `workflows_wait` | Wait for a prompt to appear in ComfyUI history. Configurable `timeout_s` and `poll_ms`. |
| `workflows_extract_from_artifact` | Extract an embedded workflow from a ComfyUI-generated image or video artifact (PNG, WebP, MP4, etc.). |
| `workflows_extract_from_photarium` | Extract a workflow for a Photarium image id, falling back to downloading the original artifact when derived JPEG variants lack embedded metadata. |
| `workflows_import_from_artifact` | Extract a workflow from an artifact and save it directly to the workflow store with name, tags, and metadata. |
| `workflows_import_from_photarium` | Pull a Photarium image workflow and save it to the workflow corpus (falls back to Photarium extras or original artifact download when needed). |
| `workflows_run_from_source` | Resolve a Photarium id, URL, or local file path, recover the embedded workflow or lineage cache, and run it. Returns `needs_input` with a resumable token when required image bindings are missing. Uses the same configured Comfy Org hidden auth as `workflows_run`. |
| `workflows_lineage_register_results` | Attach uploaded Photarium result image ids to a runtime lineage record so later requests can reuse the cached packaged workflow by result id. |
| `workflows_lineage_get` | Inspect one runtime lineage record by `lineage_run_id` or by resolving a source/result image id through the lineage indexes. |
| `workflows_run_aspect_ratio_adjustment` | Upload an image, set aspect ratio, and run the aspect ratio adjustment workflow. Supports positive/negative prompts, seed, output naming, and configured Comfy Org hidden auth. |

---

## Roadmap

- Claude Code integration
- Codex integration
- VS Code integration
- Cursor workflows
- Notion pipelines
- Astro site kits
- Cloudflare deploy targets

---

## GitHub Pages


---

## Note: Potential ComfyUI Server Fork

We are considering a fork of the ComfyUI server to accommodate revealing runtime environment details that are currently opaque to MCP clients and external tooling. Specifically:

- **Server CWD** — Expose the current working directory of the running ComfyUI server process so that MCP tools and automation scripts can resolve relative paths reliably.
- **Input folder location** — Surface the absolute path to the `input/` directory for the current live run, enabling tools to upload input images, masks, and other assets to the correct location without guesswork.
- **Output folder location** — Surface the absolute path to the `output/` directory so completed generations can be located, downloaded, and piped into downstream workflows (e.g., Photarium upload) automatically.

Currently, these paths must be hardcoded or inferred from `extra_model_paths.yaml`, which is fragile and breaks across different installations, Docker deployments, and multi-GPU setups. A lightweight API endpoint (e.g., `GET /api/server-info`) that returns these paths would make ComfyUI significantly more automatable and MCP-friendly.

If you have thoughts or interest in this, open an issue or reach out.

---

---

## Note: Potential ComfyUI Server Fork

We are considering a fork of the ComfyUI server to accommodate revealing runtime environment details that are currently opaque to MCP clients and external tooling. Specifically:

- **Server CWD** — Expose the current working directory of the running ComfyUI server process so that MCP tools and automation scripts can resolve relative paths reliably.
- **Input folder location** — Surface the absolute path to the `input/` directory for the current live run, enabling tools to upload input images, masks, and other assets to the correct location without guesswork.
- **Output folder location** — Surface the absolute path to the `output/` directory so completed generations can be located, downloaded, and piped into downstream workflows (e.g., Photarium upload) automatically.

Currently, these paths must be hardcoded or inferred from `extra_model_paths.yaml`, which is fragile and breaks across different installations, Docker deployments, and multi-GPU setups. A lightweight API endpoint (e.g., `GET /api/server-info`) that returns these paths would make ComfyUI significantly more automatable and MCP-friendly.

If you have thoughts or interest in this, open an issue or reach out.

---

## License

MIT
