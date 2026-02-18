# Workflow Import / Install README

Install workflows into the ComfyMCP workflow store from either:

- An API JSON file (`prompt` format, node-id keyed), or
- A ComfyUI-generated artifact (PNG/WebP/MP4/etc. with embedded workflow metadata).

By default, `./run_mcp_tools.sh` writes into this repo's workflow root:

- `/Users/julian/Code/nfl-comfymcp/workflows/<workflow_id>/workflow.json`
- `/Users/julian/Code/nfl-comfymcp/workflows/<workflow_id>/meta.json`
- `/Users/julian/Code/nfl-comfymcp/workflows/<workflow_id>/params.json`

## Prereqs

- Run from repo root: `/Users/julian/Code/nfl-comfymcp`
- `workflow_id` must match: letters, numbers, `_`, `-` only.
- If mutation auth is enabled, include `"token":"YOUR_TOKEN"` in tool args.

## Path A: Install from API JSON file

Use `workflows_save` (recommended).

```bash
WORKFLOW_ID="my_api_workflow"
API_JSON="/absolute/path/to/workflow_api.json"

ARGS="$(jq -cn \
  --arg workflow_id "$WORKFLOW_ID" \
  --argjson workflow_json "$(jq -c . "$API_JSON")" \
  '{
    workflow_id: $workflow_id,
    workflow_json: $workflow_json,
    meta: {
      name: $workflow_id,
      description: "Imported from API JSON",
      tags: ["imported", "api-json"]
    }
  }'
)"

./run_mcp_tools.sh call --tool workflows_save --args "$ARGS"
```

Notes:

- `workflow_json` should be Comfy API/prompt format (not UI graph format).
- `workflows_save` always regenerates packaged `meta.json` and `params.json`.

## Path B: Import directly from ComfyUI artifact image/video

Use `workflows_import_from_artifact` (extract + save + package in one step).

```bash
./run_mcp_tools.sh call \
  --tool workflows_import_from_artifact \
  --args '{
    "path":"/absolute/path/to/ComfyUI_output.png",
    "workflow_id":"my_imported_workflow",
    "name":"My Imported Workflow",
    "tags":["imported","comfyui"]
  }'
```

## Optional: Extract only (no install)

```bash
./run_mcp_tools.sh call \
  --tool workflows_extract_from_artifact \
  --args '{"path":"/absolute/path/to/ComfyUI_output.png"}'
```

If your goal is installation, `workflows_import_from_artifact` is simpler.

## Verify install

```bash
./run_mcp_tools.sh call --tool workflows_get --args '{"workflow_id":"my_imported_workflow"}'
./run_mcp_tools.sh call --tool workflows_params_get --args '{"workflow_id":"my_imported_workflow"}'
ls -la "/Users/julian/Code/nfl-comfymcp/workflows/my_imported_workflow"
```

## Optional: refine metadata later

```bash
./run_mcp_tools.sh call \
  --tool workflows_package \
  --args '{
    "workflow_id":"my_imported_workflow",
    "hints":{
      "description":"Short description of what this workflow does.",
      "use_cases":["example use case"],
      "tags":["image-edit","qwen"]
    }
  }'
```

## Fallback: filesystem copy + package (API JSON)

If you want to install from disk first, then package:

```bash
WORKFLOW_ID="my_api_workflow"
API_JSON="/absolute/path/to/workflow_api.json"

mkdir -p "/Users/julian/Code/nfl-comfymcp/workflows/$WORKFLOW_ID"
cp "$API_JSON" "/Users/julian/Code/nfl-comfymcp/workflows/$WORKFLOW_ID/workflow.json"

./run_mcp_tools.sh call \
  --tool workflows_package \
  --args "{
    \"workflow_id\":\"$WORKFLOW_ID\",
    \"hints\":{\"description\":\"Imported from API JSON\"}
  }"
```

## Common issues

1. `File not found`
- Verify `path`/`API_JSON` exists on the same machine where tools run.

2. `No embedded workflow found`
- The artifact may not include Comfy metadata.
- Try a source file generated directly by ComfyUI.

3. `Workflow id must be alphanumeric, dash, or underscore`
- Rename id to match `[A-Za-z0-9_-]+`.

4. `Workflow payload exceeds size limit`
- Default limit is `5_000_000` bytes.
- Increase `COMFY_MCP_MAX_WORKFLOW_BYTES` if needed.

5. `Readonly mode` / mutation denied
- Disable readonly mode or pass required `token`.

6. JSON installed but run fails
- Confirm the JSON is API/prompt format compatible with Comfy `/prompt`.

## Related tools

- `workflows_extract_from_artifact`: extract only
- `workflows_import_from_artifact`: extract + install
- `workflows_save`: install from API JSON payload
- `workflows_package`: regenerate packaged `meta.json` + `params.json`
