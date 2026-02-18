# Codex Micro-Plan: Phase 1 (History Store + Minimal MCP Tools)

## Goal (Single Session)

Implement only the minimum needed to start capturing workflow execution history and expose it via MCP tools.

This micro-plan is intentionally narrow so desktop Codex can complete it quickly and safely.

---

## In Scope

1. Add SQLite-backed workflow run history store.
2. Add two MCP tools (aligned with existing `workflows_*` namespace):
   - `workflows_runs_log`
   - `workflows_runs_recent`
3. Register tools in MCP server.
4. Add automatic logging hooks from workflow execution tools to the history store.
5. Add targeted unit tests.

## Out of Scope

- Semantic retrieval.
- Ranking/recommendation logic.
- Orchestrator policy changes.
- Feedback scoring.

---

## Files to Add

- `src/comfy_mcp/recommendation/history_store.py`
- `src/comfy_mcp/mcp_server/tools_recommendation.py`
- `tests/unit/test_recommendation_history_store.py`
- `tests/unit/test_mcp_tools_recommendation.py`

## Files to Update

- `src/comfy_mcp/mcp_server/server.py` (register new tools and wire execution logging path)
- `src/comfy_mcp/config/models.py` (add config field for history DB path)

---

## Data Model (MVP)

DB location (config-driven):

- `AppConfig.workflow_history_db_path` (default: `<workflow_library_root>/data/workflow_history.db`)
- Env override: `COMFY_MCP_WORKFLOW_HISTORY_DB_PATH`

Table: `workflow_runs`

- `run_id` TEXT PRIMARY KEY
- `created_at` TEXT NOT NULL
- `workflow_id` TEXT NOT NULL
- `user_query` TEXT
- `normalized_query` TEXT (derived server-side from `user_query`, never required as tool input)
- `overrides_json` TEXT
- `status` TEXT NOT NULL (`success` | `error`)
- `error_message` TEXT
- `output_refs_json` TEXT

Keep schema to one table for this phase.

---

## Tool Contracts

### `workflows_runs_log`

Input:

- `run_id` (optional; generate UUID if missing)
- `workflow_id` (required)
- `user_query` (optional)
- `overrides` (object, optional)
- `status` (`success`/`error`, required)
- `error_message` (optional)
- `output_refs` (array of strings/objects, optional)

Output:

- `{ ok: true, run_id: "...", created_at: "..." }`

### `workflows_runs_recent`

Input:

- `limit` (optional, default 20, max 200)
- `workflow_id` (optional filter)
- `status` (optional filter)

Output:

- `{ ok: true, items: [ ... ] }`

---

## Implementation TODOs

- [ ] Create `HistoryStore` class with:
  - [ ] lazy DB init,
  - [ ] schema creation (`CREATE TABLE IF NOT EXISTS`),
  - [ ] `log_run(...)`,
  - [ ] `list_recent(...)`.
- [ ] Add `AppConfig.workflow_history_db_path` with env-backed override and resolved helper.
- [ ] Serialize `overrides`/`output_refs` as compact JSON.
- [ ] Add `tools_recommendation.py` with two handlers and schemas.
- [ ] Register `workflows_runs_log` and `workflows_runs_recent` in server tool registry and dispatch.
- [ ] Wire automatic log writes from `workflows_run` and `workflows_run_aspect_ratio_adjustment` execution outcomes.
- [ ] Add unit tests for store insert/query and tool behavior.
- [ ] Add unit tests for auto-log hook behavior on success and error paths.
- [ ] Ensure tests pass with targeted pytest commands.

---

## Acceptance Criteria

- `workflows_runs_log` successfully persists a run.
- `workflows_runs_recent` returns newest-first rows.
- `workflows_runs_recent` filters by `workflow_id` and `status`.
- Repeated app starts do not break schema creation.
- History DB path resolves from config and env override.
- Normal use of `workflows_run*` creates history rows without requiring manual `workflows_runs_log` calls.
- New tests pass locally.

---

## Suggested Test Command

```bash
.venv/bin/python -m pytest \
  tests/unit/test_recommendation_history_store.py \
  tests/unit/test_mcp_tools_recommendation.py -q
```

---

## Desktop Codex Kickoff Prompt

"Implement CODEX_SMART_WORKFLOW_PHASE1_MICROPLAN.md in nfl-comfymcp. Add SQLite HistoryStore and MCP tools workflows_runs_log/workflows_runs_recent, register tools in server.py, wire auto-logging from workflows_run/workflows_run_aspect_ratio_adjustment, and add targeted unit tests. Keep scope strictly to this file and phase only. Run only the listed focused tests."
