# Codex Handoff: Smart Workflow Selection Implementation Plan

## Objective

Implement a smarter workflow-selection system in `nfl-comfymcp` that:

1. Uses semantic history (and Photarium similarity) to recommend workflows when user intent is open-ended.
2. Always honors explicit workflow requests (explicit workflow selection is a hard override).
3. Learns from run outcomes over time.

---

## Product Rules (Non-Negotiable)

- If user explicitly names a workflow, run that workflow directly.
- Only use recommendation logic when no explicit workflow is specified.
- Return recommendation rationale + confidence.
- If confidence is low, ask a clarifying question instead of forcing a workflow.

---

## Scope (MVP)

### In scope

- Persist workflow run history (prompt, workflow id, outputs, status).
- Recommend workflows from semantic retrieval + workflow metadata + historical win rate.
- Add MCP tools for recommendation and logging.
- Integrate selection policy into TUI/orchestrator behavior.

### Out of scope (MVP)

- Full retraining pipeline.
- Complex online learning.
- Cross-user personalization.

---

## Existing Context to Reuse

- Workflow metadata and search in `workflows/*/meta.json` and existing workflow tools.
- Photarium embedding/search capabilities already documented and available in the companion project.
- TUI orchestrator pipeline already supports multi-step tool use and local commands.

---

## Deliverables

1. Recommendation domain modules.
2. History persistence (SQLite).
3. New MCP tools:
   - `workflow_runs_log`
   - `workflow_runs_recent`
   - `workflows_recommend`
   - `workflows_feedback_record`
4. Orchestrator routing policy update for explicit override + smart fallback.
5. Unit tests + docs.

---

## File Plan

Create/modify these files:

- `src/comfy_mcp/recommendation/history_store.py`
- `src/comfy_mcp/recommendation/retrieval_bridge.py`
- `src/comfy_mcp/recommendation/ranker.py`
- `src/comfy_mcp/recommendation/policy.py`
- `src/comfy_mcp/mcp_server/tools_recommendation.py`
- `src/comfy_mcp/mcp_server/server.py` (tool registration)
- `src/comfy_mcp/tui_client/orchestrator.py` (selection policy hook)
- `tests/unit/test_recommendation_history_store.py`
- `tests/unit/test_recommendation_ranker.py`
- `tests/unit/test_recommendation_policy.py`
- `tests/unit/test_mcp_tools_recommendation.py`
- `README.md` (feature summary)

---

## Data Model (SQLite)

Database path:

- `data/workflow_history.db`

Tables:

### `workflow_runs`
- `run_id` TEXT PRIMARY KEY
- `created_at` TEXT
- `workflow_id` TEXT
- `user_query` TEXT
- `normalized_query` TEXT
- `overrides_json` TEXT
- `status` TEXT (`success` | `error`)
- `error_message` TEXT NULL

### `workflow_run_outputs`
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `run_id` TEXT
- `photarium_image_id` TEXT NULL
- `output_url` TEXT NULL
- `mime_type` TEXT NULL

### `workflow_feedback`
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `run_id` TEXT NULL
- `workflow_id` TEXT
- `feedback` INTEGER (`1` or `-1`)
- `created_at` TEXT

---

## Ranking Formula (MVP)

Use a transparent weighted score:

- semantic_similarity: 0.45
- workflow_metadata_match: 0.20
- historical_success_rate: 0.20
- recency_boost: 0.10
- user_feedback_bias: 0.05

Return for each candidate:

- `workflow_id`
- `score`
- `confidence`
- `reasons[]`

---

## Phase Plan with TODOs

## Phase 1: History Store

- [ ] Add SQLite schema creation/migration-on-start.
- [ ] Implement `log_run(...)` and `list_recent_runs(...)`.
- [ ] Implement `log_feedback(...)` and aggregate helpers.
- [ ] Add tests for insert/read/update semantics.

Acceptance criteria:
- Run and output records persist across process restarts.
- History queries return deterministic ordering and limits.

## Phase 2: Retrieval Bridge

- [ ] Implement bridge method `find_similar_outputs(query, limit)`.
- [ ] Wire to existing Photarium semantic search endpoint/tool.
- [ ] Map image hits to candidate workflow ids via run-output linkage.
- [ ] Add fallback behavior when semantic search unavailable.

Acceptance criteria:
- For sample queries, bridge returns candidate workflow ids + similarity scores.

## Phase 3: Ranking Engine

- [ ] Implement `rank_workflows(query, candidates, metadata, stats)`.
- [ ] Add confidence bucketing (`high`, `medium`, `low`).
- [ ] Add explanation strings for each top candidate.
- [ ] Add unit tests with fixture candidate data.

Acceptance criteria:
- Ranking is stable and explainable.
- Tests verify ordering for known fixtures.

## Phase 4: MCP Tooling

- [ ] Add `workflow_runs_log` tool.
- [ ] Add `workflow_runs_recent` tool.
- [ ] Add `workflows_feedback_record` tool.
- [ ] Add `workflows_recommend` tool using retrieval + ranking.
- [ ] Register tools in MCP server for stdio and HTTP listings.
- [ ] Add tool-level tests.

Acceptance criteria:
- Tools appear in list-tools.
- End-to-end recommend call returns ranked candidates.

## Phase 5: Orchestrator Policy Integration

- [ ] Add explicit workflow detection utility (exact id/name/alias match).
- [ ] If explicit workflow detected, bypass recommendation.
- [ ] Otherwise call `workflows_recommend` and select candidate by confidence policy.
- [ ] If low confidence, ask clarifying question.
- [ ] Log selection rationale into chat/tool transcript.

Acceptance criteria:
- Explicit workflow request never triggers recommendation path.
- Non-explicit prompt triggers recommendation path.

## Phase 6: Evaluation + Rollout

- [ ] Add offline evaluation script for recommendation precision@k.
- [ ] Add feature flag: `SMART_WORKFLOW_SELECTION=true|false`.
- [ ] Run in shadow mode first (recommend but do not auto-run).
- [ ] Promote to assist mode, then auto mode for high confidence only.

Acceptance criteria:
- Recommendation acceptance and first-try success metrics available.

---

## Confidence Policy (MVP)

- `high` (>= 0.75): auto-select top workflow.
- `medium` (0.55–0.74): present top 2–3 and ask user to choose.
- `low` (< 0.55): ask clarifying prompt (style/content/constraints) before run.

---

## Test Plan

- Unit tests for store, ranker, policy.
- MCP tool tests for schema and outputs.
- One integration test for “non-explicit query -> recommend -> select”.
- One integration test for “explicit workflow -> direct run”.

---

## Risks and Mitigations

- Sparse history at cold start → fallback to metadata search.
- Missing output provenance links → enforce run logging pipeline.
- Noisy semantic matches → confidence thresholds + clarify flow.

---

## Suggested First Codex Task

Start with Phase 1 + Phase 4 minimal:

1. Implement `history_store.py`.
2. Add `workflow_runs_log` + `workflow_runs_recent`.
3. Add tests.
4. Wire into server tool registry.

This creates immediate value and enables later recommendation phases.

---

## Codex Prompt to Use

Use this in desktop Codex as a kickoff instruction:

"Implement Phase 1 and Phase 4 (minimal) from CODEX_SMART_WORKFLOW_IMPLEMENTATION_PLAN.md in nfl-comfymcp. Add SQLite-backed workflow run history persistence, expose MCP tools workflow_runs_log and workflow_runs_recent, register them in server.py, and add unit tests. Keep changes minimal and consistent with project style. Run targeted pytest tests for new modules."
