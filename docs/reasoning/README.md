# MCP Reasoning And Orchestration Plan

## Why This Exists

The current tool surface can run workflows reliably when arguments are explicit, but free-form goal execution is inconsistent:

- The model can miss existing `params.json` contracts and invent workarounds.
- A specialized path (`workflows_run_aspect_ratio_adjustment`) works better than generic planning.
- Multi-step objectives (generate batch -> transform ratios -> targeted edit -> upload variant) are not first-class.

This plan upgrades reasoning from "single tool-call guessing" to a structured planner/executor with memory and workflow introspection.

## What "Richer Context" Means In Practice

Use these context layers together:

1. Tool contract context
- Improve MCP tool descriptions and input schemas so required fields and semantics are explicit.
- Expose compact discovery tools that summarize workflows and params in LLM-friendly form.

2. Workflow capability context
- Treat each workflow as a typed capability card (`meta.json` + `params.json` + optional examples).
- Add tags and "when to use" hints to metadata.

3. Runtime state context
- Track downloaded assets, generated outputs, prompt IDs, and parent-child relationships.
- Feed this back to the model every turn as a compact state object.

4. Historical memory context
- Persist successful plans and overrides by intent class.
- Reuse those recipes on similar future requests.

## Target End State

The orchestrator should:

1. Parse user goals into tasks and constraints.
2. Select workflows using capability cards (not node-level guesswork).
3. Validate required params before execution.
4. Execute a multi-step plan with retries and fallback paths.
5. Persist artifacts + execution lineage for follow-on edits.
6. Explain what it will do before tool calls and summarize results after.

## Current Gaps (Repo-Specific)

- `workflows_run` already expects `overrides` and uses `params.json`, but the model can still choose wrong call patterns.
- `workflows_run_aspect_ratio_adjustment` is hardcoded for one workflow and bypasses generic capability discovery.
- System prompt in `mcp_chat_config.json` carries important rules, but they are long-form and not enforced by code.
- `ChatOrchestrator` (`src/comfy_mcp/tui_client/orchestrator.py`) is a generic tool loop; it has no explicit planning state machine.
- No durable memory of prior successful tool chains per objective type.

## Design Principles

- Prefer declarative capability metadata over handcrafted workflow-specific code.
- Keep tool surfaces composable and typed.
- Validate early (`dry-run`) before queueing expensive jobs.
- Keep model context compact and structured (JSON summaries, not raw large blobs).
- Separate planning policy from transport concerns (TUI, HTTP, MCP router).

## Architecture Proposal

## 1) Capability Index Layer

Create a capability index built from `workflows/*/{meta.json,params.json}`:

- Each capability card includes:
  - `workflow_id`
  - `name`, `description`, `tags`
  - required params
  - optional params with defaults
  - accepted enums/constraints
  - recommended use-cases (new metadata field)
  - sample override payloads (new optional field)

Implementation options:

- Add helper module: `src/comfy_mcp/workflow_store/capabilities.py`
- Add MCP tool: `workflows_capabilities_list` (compact summaries)
- Add MCP tool: `workflows_capabilities_get(workflow_id)` (full card)

## 2) Plan/Execute State Machine

Add a deterministic orchestration layer above tool-calling:

- `Plan` phase:
  - classify intent (`generate`, `edit`, `resize`, `publish`, `variant`)
  - decompose into steps
  - map steps to workflows/tools
- `Prepare` phase:
  - resolve assets (download/upload paths)
  - fill required params
  - run validation
- `Execute` phase:
  - call tools in order
  - capture prompt IDs + outputs
- `Reflect` phase:
  - detect failures
  - retry with corrected params or fallback workflow

Add module:

- `src/comfy_mcp/tui_client/planner.py` (intent + plan construction)
- `src/comfy_mcp/tui_client/executor.py` (step execution and retry policy)

## 3) Validation-First Workflow Execution

Add a dry-run and explain surface:

- New MCP tool `workflows_validate_run`:
  - inputs: `workflow_id`, `overrides`, `force?`
  - output: missing/unknown/invalid params + normalized overrides
- New MCP tool `workflows_explain_requirements`:
  - inputs: `workflow_id`
  - output: required params, constraints, and example payload

This reduces hallucinated fields and avoids queue failures.

## 4) Memory + Lineage

Persist structured execution memory:

- `runs/history.jsonl` or lightweight SQLite table
- record:
  - user goal summary
  - chosen plan
  - tool calls
  - workflow IDs + overrides used
  - output filenames/view URLs
  - parent image IDs and variant lineage
  - success/failure + error class

Use memory during planning:

- retrieve top similar successful runs by tags/intent
- suggest known-good workflow chains and parameter presets

## 5) Prompt-Context Compiler

Replace one long static system prompt with assembled context blocks per turn:

- core policy block (stable invariants)
- current capability shortlist (top-N relevant workflow cards)
- current asset state block
- recent successful recipes block

Add module:

- `src/comfy_mcp/tui_client/context_builder.py`

Update:

- `src/comfy_mcp/tui_client/app.py`
- `src/comfy_mcp/tui_client/orchestrator.py`

to rebuild context each user turn.

## Metadata Schema Extensions

Extend `meta.json` (backward compatible):

```json
{
  "id": "image_edit",
  "name": "Qwen Image Edit",
  "description": "Edit an existing image.",
  "tags": ["image-edit", "img2img"],
  "use_cases": ["change garment material", "style transfer while preserving composition"],
  "io_contract": {
    "inputs": ["local_image_path_or_uploaded_filename", "positive_prompt"],
    "outputs": ["output_images[]"]
  },
  "examples": [
    {
      "goal": "Change clothing to denim",
      "overrides": {
        "image_filename": "input.jpg",
        "positive_prompt": "keep pose and lighting; convert outfit to denim",
        "negative_prompt": "distorted anatomy, extra limbs"
      }
    }
  ]
}
```

Keep authoritative field validation in `params.json`; metadata is discovery guidance.

## Phase Plan

## Phase 0: Baseline And Safety (1-2 days)

1. Add instrumentation to capture tool-call failures by type.
2. Add regression fixtures for current good flows (aspect ratio, image edit).
3. Remove plaintext API keys from checked-in config and rely on env vars.

Acceptance:

- Failures categorized in logs.
- Existing passing tests still pass.

## Phase 1: Capability Discovery Upgrade (2-3 days)

1. Implement capability index builder from store metadata + params.
2. Add `workflows_capabilities_list/get` tools.
3. Improve tool descriptions in `src/comfy_mcp/mcp_server/server.py` with explicit required patterns.

Acceptance:

- LLM can retrieve one compact card instead of full workflow JSON for discovery.

## Phase 2: Validation-First Execution (1-2 days)

1. Add `workflows_validate_run`.
2. Add `workflows_explain_requirements`.
3. Update system prompt policy to require validate-before-run when unknown.

Acceptance:

- Unknown/missing params are caught before queueing.

## Phase 3: Planner + Executor (3-5 days)

1. Add `planner.py` and `executor.py`.
2. Introduce step objects (`type`, `tool`, `args`, `depends_on`).
3. Add retry policy for common failures (missing params, bad image path, transient queue issues).

Acceptance:

- Multi-step request produces explicit ordered plan and completes end-to-end in one turn loop.

## Phase 4: Memory + Recipe Reuse (2-4 days)

1. Persist run history and lineage.
2. Retrieve similar prior successful runs during planning.
3. Add optional user-facing "why this plan" explanation with references to prior successful pattern.

Acceptance:

- Repeated objective classes show fewer exploratory tool calls.

## Phase 5: Specialized Workflow Wrappers As Plugins (optional)

Refactor hardcoded specialized tools into plugin specs:

- each plugin declares:
  - supported objective class
  - param mappings
  - pre/post hooks

This keeps "works like butter" behavior without one-off hardcoded logic in `tools_workflows.py`.

## Example: Magazine Pipeline Objective

User goal:
"Produce 10 editorial fashion images, each in 1:1, 4:5, and 9:16, then edit one selected image to denim and upload as variant."

Planned chain:

1. `klein_flux2_text_to_image` x10 (base creative set)
2. aspect-ratio workflow for each selected image x2 extra ratios
3. `image_edit` on chosen asset with garment-change prompt
4. upload output as Photarium variant with parent linkage
5. store run recipe and lineage

What improves:

- The model chooses from declared capabilities.
- Param filling is validated before execution.
- Outputs and provenance are persisted for follow-on edits.

## Testing Strategy

Add/extend tests:

- `tests/unit/test_mcp_tools.py`
  - capability list/get responses
  - validate-run error classes
- `tests/unit/test_tui_orchestrator.py`
  - planner emits deterministic step sequence
  - executor handles retries
- integration smoke:
  - generate -> resize -> edit -> upload variant chain

Include golden snapshots for:

- capability card format
- plan format
- memory record format

## Migration Notes

- Existing `workflows_run` remains source of truth for execution.
- Existing `meta.json` and `params.json` remain valid; new fields are optional.
- Specialized tool paths can be preserved initially and gradually migrated to plugin specs.

## Immediate Next Actions

1. Implement `workflows_capabilities_list/get` and wire tests.
2. Implement `workflows_validate_run`.
3. Add context builder that injects top-N capability cards each turn.
4. Add run history persistence and basic retrieval by tags.

