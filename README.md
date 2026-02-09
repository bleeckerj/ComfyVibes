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

### TUI Chat Client

This is very much a work-in-progress, but you can have conversations with your ComfyUI server right from the terminal. It’s a great (or frustrating) way to explore the tool surface, test out prompts, and see how LLMs can orchestrate workflows in real time.

```bash
./run_mcp_chat.sh
```

`./run_mcp_chat.sh` runs a doctor preflight first and exits with clear endpoint/service errors if anything is down.

If you want direct command form instead of the launcher:

```bash
comfy-mcp-chat --config mcp_chat_config.json
```

OpenAI key can be set in either:

- `mcp_chat_config.json` at `llm.api_key`
- environment var named in `llm.api_key_env`

Launchers:

- `./run_mcp_chat.sh`: copy-friendly mode (mouse capture off; terminal selection works)
- `./run_mcp_chat_mouse.sh`: pane wheel-scroll mode (mouse capture on)

Reliability mode:

- Set `"strict_tool_facts": true` in `mcp_chat_config.json` to force tool-grounded output.
- In this mode, when tools are called, the chat shows exact tool JSON and skips model interpretation.

Keybindings:

- `F1`: Set active pane to chat
- `F2`: Set active pane to tools
- `Alt+Up/Down`: Scroll active pane by one line
- `Alt+PageUp/PageDown`: Scroll active pane by one page
- `Alt+Home/End`: Jump active pane to top/bottom
- `F6`: Copy chat pane content to clipboard
- `F7`: Copy tool pane content to clipboard
- `F8`: Copy both chat and tool content to clipboard
- `F9`: Export full transcript to `mcp_chat_transcript_YYYYMMDD_HHMMSS.txt`
- `Ctrl+Q`, `Ctrl+C`, `Esc`, or `F10`: Quit

### Deterministic Tool Runner (No LLM)

Use this for fast, explicit tool calls with zero model reasoning.

```bash
./run_mcp_tools.sh doctor
./run_mcp_tools.sh list-tools
./run_mcp_tools.sh call --tool photarium_get --args '{"imageId":"YOUR_IMAGE_ID"}'
```

Run multi-step jobs from JSON:

```bash
./run_mcp_tools.sh run-steps --steps-file steps.json
```

`steps.json` format:

```json
[
  {"tool": "photarium_get", "args": {"imageId": "YOUR_IMAGE_ID"}},
  {"tool": "comfy_queue_get", "args": {}}
]
```

`doctor` checks:

- ComfyUI HTTP endpoint reachability
- Photarium HTTP endpoint reachability
- MCP server startup and tool listing
- Basic smoke tool calls (`comfy_queue_get`, `photarium_list`)

Minimal config example:

```json
{
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "model": "gpt-4o-mini",
    "temperature": 0.2,
    "timeout_s": 180
  },
  "system_prompt": "You are a terminal chat orchestrator. Decide when to call MCP tools, then summarize results for the user.",
  "servers": [
    {
      "name": "comfy",
      "command": "python",
      "args": ["-m", "comfy_mcp.mcp_server.cli"],
      "tool_prefixes": ["comfy_", "workflows_"]
    }
  ]
}
```

If you see `LLM request timed out`, increase `llm.timeout_s` (for example `180` or `300`).

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

## MCP Tool Surface

ComfyVibes exposes two MCP servers — **ComfyMCP** for ComfyUI interaction and workflow management, and **Photarium** for image gallery, search, and asset management via Cloudflare Images. Together they provide **65+ tools** for end-to-end creative automation.

---

### ComfyMCP Tools (17 tools)

#### ComfyUI Read Tools

| Tool | Description |
|------|-------------|
| `comfy_nodes_list` | Return the full ComfyUI node catalog — every available node type and its parameters. |
| `comfy_queue_get` | Return the current ComfyUI queue state, including pending and running prompts. |
| `comfy_history_get` | Return ComfyUI history data. Optionally filter by `prompt_id` to get results for a specific run. |
| `comfy_models_list` | List all available model folders (checkpoints, loras, vae, embeddings, etc.). |
| `comfy_models_get` | List available model files within a specific folder (e.g., all checkpoint files). |
| `comfy_embeddings_list` | Return all available embedding names for use in prompts. |
| `list_tools` | Return tool definitions for this MCP server (self-documenting). |

#### Workflow Management Tools

| Tool | Description |
|------|-------------|
| `workflows_list` | List all workflows stored in the workflow store with metadata. |
| `workflows_get` | Return the full workflow JSON and metadata for a given `workflow_id`. |
| `workflows_params_get` | Return the `params.json` for a workflow — the tunable parameter spec. |
| `workflows_save` | Save a workflow entry to the store with optional metadata, params, and auth token. |
| `workflows_delete` | Delete a workflow entry from the store by `workflow_id`. |

#### Workflow Execution Tools

| Tool | Description |
|------|-------------|
| `workflows_run` | Run a workflow with parameter overrides via ComfyUI. Supports `force` to bypass caching and `client_id` for tracking. |
| `workflows_wait` | Wait for a prompt to appear in ComfyUI history. Configurable `timeout_s` and `poll_ms`. |
| `workflows_extract_from_artifact` | Extract an embedded workflow from a ComfyUI-generated image or video artifact (PNG, WebP, MP4, etc.). |
| `workflows_import_from_artifact` | Extract a workflow from an artifact and save it directly to the workflow store with name, tags, and metadata. |
| `workflows_run_aspect_ratio_adjustment` | Upload an image, set aspect ratio, and run the aspect ratio adjustment workflow. Supports positive/negative prompts, seed, and output naming. |

---

### Photarium Tools (48 tools)

#### Search & Discovery

| Tool | Description |
|------|-------------|
| `photarium_search` | **Semantic search** using natural language and CLIP embeddings. Finds images by concept, subject, mood, or visual characteristics — even without exact text matches. |
| `photarium_search_text` | **Text search** matching against image metadata: filename, folder name, tags, description, and alt text. Best when you know exact names or tags. |
| `photarium_search_color` | Search for images by **dominant color**. Accepts hex codes (`#3B82F6`) or named colors (`red`). Uses color embeddings for accurate matching. |
| `photarium_search_image` | Find images **visually similar** to a given image using its CLIP embedding. No text query needed. |
| `photarium_similar` | Find visually or chromatically similar images. Supports `clip` (semantic) and `color` (palette) modes, with optional "strangers" for contrast. |
| `photarium_antipode` | Find **semantic or color opposites** of an image. CLIP methods: `negate`, `stranger`, `otherwise`, `reflectroid`. Color methods: `complementary`, `histogram`, `lightness`, `negative`. |
| `photarium_concepts` | Get **semantic concept scores** — how AI interprets visual qualities along dimensions like warm/cold, minimal/complex, playful/serious. |
| `photarium_haiku` | Generate a haiku inspired by an image's semantic qualities (CLIP embedding). |

#### Gallery & Image Management

| Tool | Description |
|------|-------------|
| `photarium_list` | List images with optional filtering by folder, namespace, and aspect ratio class (square, horizontal, vertical). |
| `photarium_get` | Get detailed info about a specific image — metadata, dimensions, variant URLs. |
| `photarium_download_image` | Download an image by ID. Returns base64 data + metadata. Optionally save to a local file path. |
| `photarium_share_url` | Get a shareable redirect URL for an image at a specific variant size (thumbnail, small, medium, large, xlarge). |
| `photarium_rotate` | Rotate an image server-side (left, right, custom degrees, or auto-EXIF) and re-upload to Cloudflare. |
| `photarium_delete` | **Permanently delete** a single image from the gallery. |
| `photarium_delete_family` | Delete an entire image family (parent + all variants). Supports dry-run and async modes with job polling. |
| `photarium_delete_family_job` | Poll the status of an async delete-family job by `jobId`. |
| `photarium_swap_parent` | Swap the parent image for a variant family. Useful for promoting a variant to primary. |

#### Upload & Import

| Tool | Description |
|------|-------------|
| `photarium_upload_from_path` | **Upload directly from a file path** using multipart form data. No base64 encoding needed — fast and efficient for local files. |
| `photarium_upload_url` | Upload an image from a URL. Downloaded and stored in Cloudflare Images with optional folder, tags, and metadata. |
| `photarium_upload_file` | Upload a file via base64 data. Supports zip/Keynote bundles and metadata fields. |
| `photarium_upload_image` | Convenience upload for base64 image data with full metadata support. |
| `photarium_upload_external_file` | Upload via the external upload endpoint — intended for lightweight external tools. |
| `photarium_import_url` | Import a remote image URL and return base64 data + metadata for client-side upload workflows. |
| `photarium_animate` | Create an **animated WebP** from a sequence of frames (URLs or base64) and upload to Cloudflare Images. |
| `photarium_uploads_list` | List paginated uploads with canonical Cloudflare URLs and metadata. |
| `photarium_upload_download` | Download a specific upload by ID, returning base64 data + metadata. |

#### Metadata & Organization

| Tool | Description |
|------|-------------|
| `photarium_update_metadata` | Update image metadata: folder, tags, description, alt text, namespace, parent-child relationships, display name, and source URLs. |
| `photarium_extras_get` | Get additional image extras stored outside of Cloudflare metadata (custom descriptions, alt text overrides). |
| `photarium_extras_update` | Update image extras. Set description or alt text to `null` to clear. |
| `photarium_list_folders` | List all available folders in the gallery, optionally filtered by namespace. |
| `photarium_create_folder` | Create a new folder for organizing images. |
| `photarium_list_namespaces` | List all registered namespaces for multi-tenant image organization. |

#### AI Generation

| Tool | Description |
|------|-------------|
| `photarium_generate_alt` | Generate accessibility **alt text** for an image using AI vision. Saved to metadata automatically. |
| `photarium_generate_description` | Generate a detailed **AI description** of an image. Saved to metadata. |
| `photarium_generate_prompt` | Generate a **text-to-image prompt** that could recreate an image. Useful for prompt engineering and understanding visual style. |
| `photarium_prompt_get` | Get the stored PromptThis record for an image. |
| `photarium_prompts_bulk` | Fetch stored prompts for multiple images in a single request. |

#### Embeddings & Vector Search

| Tool | Description |
|------|-------------|
| `photarium_generate_embeddings` | Generate CLIP and/or color embeddings for an image, enabling semantic and color search. |
| `photarium_embedding_status` | Check embedding status (CLIP/color) for a specific image. |
| `photarium_embeddings_batch` | Generate embeddings for multiple images in a single batch. |
| `photarium_colors_bulk` | Fetch color metadata (dominant colors, average color) for multiple images. |
| `photarium_vector_status` | Check vector search system status: Redis availability, embedding progress, index statistics. |
| `photarium_vector_index` | Ensure the vector index exists — creates it if missing. |

#### Audit & Backup

| Tool | Description |
|------|-------------|
| `photarium_audit` | Audit CDN URLs and report broken or failing image variants. Configurable concurrency and verbosity. |
| `photarium_backup` | Trigger a Redis database backup (RDB snapshot + compressed bundle). Auto-rotates old backups. |
| `photarium_list_backups` | List existing Redis backups with timestamps, sizes, and types. |

#### Debug

| Tool | Description |
|------|-------------|
| `photarium_debug_raw` | Fetch raw Cloudflare Images API data for debugging. |
| `list_tools` | Return tool definitions for the Photarium MCP server (self-documenting). |

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

## License

MIT
