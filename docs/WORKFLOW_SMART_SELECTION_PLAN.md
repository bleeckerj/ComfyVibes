# Smart Workflow Selection Plan

## Purpose

Build a **workflow recommendation facility** that selects likely-good Comfy workflows from historical outcomes when the user asks for an image in natural language.

This serves two goals:

1. **Better first-try results** by reusing workflows that previously produced semantically similar outputs.
2. **Lower interaction cost** by reducing manual workflow browsing/guessing.

---

## Product Behavior (Decision Policy)

### Hard rule: explicit workflow always wins

If the user specifies a workflow by id/name (for example: `image_edit`, `8-bit-image-maker`, “use the Qwen image edit workflow”), the system must:

- skip semantic workflow recommendation,
- run the requested workflow directly,
- still log the run outcome for learning.

### Smart mode: only when workflow is not explicit

If user intent is open-ended (for example: “make this look like vintage pixel art”), the system should:

1. Retrieve semantically similar images from history/catalog.
2. Map those images to workflows that produced them.
3. Rank candidate workflows.
4. Return top recommendations with confidence + rationale.
5. Use best candidate by default (or ask user to confirm when confidence is low).

---

## Current Foundation You Already Have

- Workflow metadata and text/tag search in `nfl-comfymcp` (`workflows_search`, `meta.json` patterns).
- Photarium embedding + similarity/search infrastructure in `cloud-flare-image-handler`.
- MCP orchestration and tool-calling loop in TUI.

This plan connects those pieces into a coherent recommendation loop.

---

## Proposed Architecture

## 1) Run History Ledger (new in `nfl-comfymcp`)

Store every workflow execution with:

- `run_id`
- `timestamp`
- `workflow_id`
- `prompt_text` (normalized)
- `overrides` (seed, cfg, steps, etc.)
- `input_image_refs`
- `output_image_refs` (Photarium IDs and/or URLs)
- `status` (`success`/`error`)
- optional user rating (`thumb_up`, `thumb_down`)

Why: this becomes the training memory for “what worked before”.

## 2) Workflow ↔ Output Image Linking

For each successful run, persist linkage:

- workflow → output images
- output image → workflow provenance

Why: enables “find similar image → infer likely workflow”.

## 3) Embedding-backed Retrieval Layer

Use existing Photarium semantic search to get nearest images for a query, then project those hits to workflow candidates.

Input signals:

- Text query embedding similarity
- (Optional) reference image similarity
- Workflow metadata text match (`meta.json` tags/description)
- Historical success rate
- Recentness (small boost)

## 4) Ranking / Selection Engine

Score workflows with transparent formula:

```text
score = 0.45 * semantic_image_similarity
      + 0.20 * workflow_metadata_similarity
      + 0.20 * success_rate
      + 0.10 * recency_boost
      + 0.05 * user_feedback_bias
```

Top output:

- `workflow_id`
- `score`
- `confidence`
- explanation snippets (e.g., “matched 12 similar outputs tagged pixel-art”).

## 5) Orchestrator Policy Integration

In TUI/orchestrator:

- Detect explicit workflow mention first.
- If explicit: route directly to `workflows_run*`.
- Else: call recommendation tool, then run selected workflow.

---

## MCP Tool Surface Additions

Add these tools in `nfl-comfymcp` MCP server:

1. `workflows_runs_log`
   - Record run outcome + output references.
2. `workflows_runs_recent`
   - Query history by workflow/date/status.
3. `workflows_recommend`
   - Input: `query`, optional `reference_image_id`, `limit`.
   - Output: ranked workflow candidates with rationale.
4. `workflows_feedback_record`
   - Record thumbs up/down by `run_id` or `workflow_id`.
5. `workflows_select_for_intent` (optional convenience wrapper)
   - Applies explicit-override logic and returns selected workflow + reason.

---

## Data Model (MVP)

Use SQLite (simple, local, reliable) via config:

- `AppConfig.workflow_history_db_path`
- default: `<workflow_library_root>/data/workflow_history.db`
- env override: `COMFY_MCP_WORKFLOW_HISTORY_DB_PATH`

Tables:

- `workflow_runs`
- `workflow_run_outputs`
- `workflow_feedback`
- `workflow_aggregate_stats` (materialized/periodic)

MVP-compatible JSON fallback can exist, but SQLite is recommended for rank queries and growth.

---

## Implementation TODOs (Phased)

## Phase 0 — Contract and Policy (1 day)

- [ ] Define explicit workflow detection rules (id/name/aliases).
- [ ] Define recommendation confidence thresholds:
  - high: auto-run,
  - medium: ask user confirm,
  - low: ask clarifying question.
- [ ] Finalize score weights and feature flags in config.

Deliverable: policy doc + config schema.

## Phase 1 — Run History Capture (1–2 days)

- [ ] Add run ledger persistence layer (SQLite).
- [ ] Automatically log successful and failed `workflows_run*` executions.
- [ ] Capture output image IDs/URLs when available.
- [ ] Add tests for insert/read/query.

Deliverable: reliable provenance store.

## Phase 2 — Retrieval Bridge to Photarium (2 days)

- [ ] Add adapter client for semantic image search endpoint/tool.
- [ ] Map retrieved images back to source workflow IDs via run ledger.
- [ ] Add fallback path when no embeddings/hits are available.

Deliverable: query → candidate workflows pipeline.

## Phase 3 — Ranking Engine + MCP Tool (2 days)

- [ ] Implement ranking function (weighted scoring).
- [ ] Expose `workflows_recommend` MCP tool.
- [ ] Return explainable ranking metadata.
- [ ] Add deterministic unit tests with fixture data.

Deliverable: recommendation API/tool.

## Phase 4 — Orchestrator Integration (1–2 days)

- [ ] Add explicit-workflow detection in TUI orchestrator policy.
- [ ] Route non-explicit requests through `workflows_recommend`.
- [ ] Add `/status` details for recommendation mode + last decision reason.
- [ ] Add transcript logging of “why this workflow was selected”.

Deliverable: end-to-end smart selection behavior in chat.

## Phase 5 — Feedback Loop + Tuning (ongoing)

- [ ] Add `workflows_feedback_record` tool.
- [ ] Include thumbs up/down in scoring bias.
- [ ] Add offline evaluation script for top-k recommendation quality.
- [ ] Tune weights from real run history.

Deliverable: continuously improving recommender.

---

## Guardrails and UX Rules

- Never override explicit user workflow request.
- Show recommendation rationale in plain language.
- Expose confidence score and allow quick override (`/use <workflow_id>` future command).
- Fall back to current metadata search when embedding search unavailable.

---

## Success Metrics

Primary:

- Top-1 recommendation acceptance rate.
- Reduced number of retries before a satisfactory output.
- Time-to-first-good-output.

Secondary:

- Frequency of explicit override after recommendation.
- Per-workflow win rate by intent category.

---

## Suggested File/Module Additions

`nfl-comfymcp`:

- `src/comfy_mcp/recommendation/history_store.py`
- `src/comfy_mcp/recommendation/retrieval_bridge.py`
- `src/comfy_mcp/recommendation/ranker.py`
- `src/comfy_mcp/recommendation/policy.py`
- `src/comfy_mcp/mcp_server/tools_recommendation.py`
- `tests/unit/test_recommendation_*.py`

Optional in `cloud-flare-image-handler`:

- endpoint/tool additions only if needed for stronger provenance lookup.

---

## Rollout Strategy

1. **Shadow mode**: compute recommendations but do not auto-apply; log comparison to actual chosen workflow.
2. **Assist mode**: suggest top-3 workflows with rationale.
3. **Auto mode**: auto-select when confidence ≥ threshold and no explicit workflow is given.

This de-risks behavior while building trust and tuning quality.

---

## Why this serves your goal

Your goal is a “smarter” workflow chooser from historical outputs + natural language intent. This implementation gives:

- provenance memory of what actually worked,
- semantic retrieval over produced imagery,
- explainable ranking over candidate workflows,
- strict user-intent override when workflow is explicitly specified.

That combination creates a practical recommendation system that is useful immediately (MVP) and improves over time with feedback.
