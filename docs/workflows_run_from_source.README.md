# Workflow Run From Source / Lineage Cache

Use `workflows_run_from_source` when you have an existing image and want ComfyMCP to:

1. resolve the image source from a Photarium id, URL, or local file path
2. extract the embedded workflow or reuse an existing cached lineage package
3. classify the workflow as text-to-image, image-to-image, or image-stitch
4. run it immediately, or return `needs_input` with a resumable token if required image bindings are missing

This feature stores packaged runtime snapshots under:

- `/Users/julian/Code/nfl-comfymcp/run_workflows/<lineage_run_id>/workflow.json`
- `/Users/julian/Code/nfl-comfymcp/run_workflows/<lineage_run_id>/params.json`
- `/Users/julian/Code/nfl-comfymcp/run_workflows/<lineage_run_id>/meta.json`
- `/Users/julian/Code/nfl-comfymcp/run_workflows/<lineage_run_id>/lineage.json`
- `/Users/julian/Code/nfl-comfymcp/run_workflows/<lineage_run_id>/execution.json`

It also maintains source/result lookup indexes so later references by Photarium id can reuse the cached package without re-extracting.

## When to use it

Use `workflows_run_from_source` when you want to say:

- "Run the workflow that made this image."
- "I forgot which workflow made image `abc123`; just run that one again."
- "Use this result image id and recover the same workflow lineage."
- "Start from this URL or local artifact and rerun the embedded workflow."

Do not use it when you already know the `workflow_id` you want and just need a normal `workflows_run`.

## Deterministic tool-runner examples

### 1. Run from a Photarium image id

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

### 2. Run from a local file path

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_run_from_source \
  --args '{
    "source":"/tmp/ComfyUI_01065.png",
    "source_kind":"file_path",
    "overrides":{"seed":123456789}
  }'
```

### 3. Run from a URL

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_run_from_source \
  --args '{
    "source":"https://example.com/comfy-output.png",
    "source_kind":"url"
  }'
```

### 4. Resume a stitch workflow that needs extra image bindings

First call:

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_run_from_source \
  --args '{
    "source":"/tmp/stitched-result.png",
    "source_kind":"file_path"
  }'
```

If the workflow is classified as `image_stitch`, the tool may return:

```json
{
  "status": "needs_input",
  "resume_token": "run_abcd1234ef56",
  "missing_required_inputs": [
    {"name": "image", "description": "LoadImage.image"},
    {"name": "image_2", "description": "LoadImage.image"}
  ]
}
```

Resume with the required bindings:

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_run_from_source \
  --args '{
    "resume_token":"run_abcd1234ef56",
    "overrides":{
      "image":"/tmp/input_a.png",
      "image_2":"/tmp/input_b.png"
    }
  }'
```

### 5. Register uploaded result ids for later reuse

If EDGAR or another client uploads the workflow outputs to Photarium, register those result ids:

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_lineage_register_results \
  --args '{
    "lineage_run_id":"run_abcd1234ef56",
    "results":[
      {"image_id":"b287f5ef-2901-4e27-f6b4-b483fc4a7e00","namespace":"cf-default"}
    ]
  }'
```

After that, the result image id itself becomes reusable as a source:

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_run_from_source \
  --args '{
    "source":"b287f5ef-2901-4e27-f6b4-b483fc4a7e00",
    "source_kind":"photarium_id",
    "namespace":"cf-default"
  }'
```

### 6. Inspect lineage by run id or image id

```bash
/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_lineage_get \
  --args '{"lineage_run_id":"run_abcd1234ef56"}'

/Users/julian/Code/nfl-mcp-tui/run_mcp_tools.sh call \
  --tool workflows_lineage_get \
  --args '{"image_id":"b287f5ef-2901-4e27-f6b4-b483fc4a7e00"}'
```

## TUI usage

There is no special slash command for this feature. In the TUI, ask for it in natural language and let EDGAR call the tool.

Examples:

- `Run the workflow embedded in Photarium image 75e92a7e-2838-45a7-6f2c-32a5fde6c300.`
- `Run the workflow from /tmp/ComfyUI_01065.png and keep the same prompt but change denoise to 0.65.`
- `Use the workflow from https://example.com/source.png and rerun it.`
- `Rerun the workflow that made image b287f5ef-2901-4e27-f6b4-b483fc4a7e00.`

If the tool returns `needs_input`, answer with the missing bindings directly:

- `Use /tmp/input_a.png for image and /tmp/input_b.png for image_2.`

Related local slash commands:

- `/importwf <image_id>` imports a Photarium workflow into the main catalog for named reuse.
- `/imageedit <image_id> <request>` runs the dedicated image-edit flow, not generic lineage recovery.
- `/vary <image_id>` runs the variation flow, not generic lineage recovery.

## Behavior notes

- `image_to_image` runs auto-bind the resolved source image.
- `image_stitch` runs only auto-bind image inputs when the lineage cache already knows the original bindings.
- `text_to_image` runs reuse embedded defaults unless you override prompt/seed/resolution fields.
- Runtime lineage cache is separate from the curated `workflows/` library.
- Result-image-id reuse only becomes authoritative after `workflows_lineage_register_results`.
