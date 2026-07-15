# ComfyUI Remote Integrations Plan

Status: implemented (2026-07-15)
Target repository: `/Users/julian/Code/nfl-comfymcp`
Source of feature ideas: `/Users/julian/Code/ComfyUI_FL-MCP`

Implementation notes:

- The laptop-side ComfyMCP architecture remains the MCP and workflow state boundary; `COMFY_MCP_COMFY_BASE_URL` is the single remote ComfyUI and Manager target.
- Client routes, partial capability discovery, policy-gated mutations, Manager v4 discovery/mutations, filtered node search, HTTP/stdio registry exposure, and focused tests are implemented.
- Read-only remote integration tests require `COMFY_MCP_LIVE_TESTS=1`; workflow execution and mutation smoke tests remain separately gated by `COMFY_MCP_LIVE_MUTATION_TESTS=1`.
- Local verification passes with the optional live tests skipped. Remote host reachability and installed Manager version remain environment-dependent checks.

## Objective

Augment ComfyMCP with the highest-value, non-browser ComfyUI capabilities while preserving its laptop-side MCP architecture:

```text
Agent / MCP client on laptop
        |
        | HTTP + WebSocket over a private network
        v
ComfyUI and ComfyUI Manager on GPU machine
```

The MCP server, workflow library, parameter reasoning, and lineage state remain on the laptop. ComfyUI remains the remote execution target.

## Scope

### In scope

- Remote ComfyUI capability and health inspection
- Job and queue operations
- Runtime controls such as interrupt and memory release
- ComfyUI settings and history operations with policy gates
- Workflow templates, global subgraphs, node replacements, assets, and tags
- ComfyUI Manager v4 discovery and confirmation-gated mutations
- Filtered node catalog search
- HTTP and stdio transport support
- Unit tests, optional live integration tests, and operator documentation

### Deferred

- Browser canvas manipulation
- Screenshots and frontend commands
- Remote filesystem administration
- Custom-node editing and patching
- Git commit/push operations on the GPU host
- ComfyUI process restart and log management

Deferred host operations require a separate authenticated GPU-host service or another explicit host-side boundary.

## Repository constraints

- Preserve the existing dirty `.mcp_chat_ui_prefs.json` file.
- Do not mix implementation changes with unrelated cleanup.
- Keep the ComfyUI client, Manager client, tool facades, policy, and transport wiring in separate modules.
- Keep secrets in `.env` or an approved secret store.
- Keep the remote ComfyUI connection on a private network, VPN, or tunnel.
- Do not expose the HTTP proxy or ComfyUI directly to an untrusted network.

## Phase 0: Baseline and compatibility

1. Confirm the remote ComfyUI HTTP and WebSocket endpoints are reachable from the laptop.
2. Confirm the remote ComfyUI version and whether ComfyUI Manager v4 is available.
3. Keep `COMFY_MCP_COMFY_BASE_URL` as the single remote target configuration.
4. Leave `COMFY_MCP_COMFY_OUTPUT_DIR` unset unless the GPU output directory is intentionally mounted locally.
5. Resolve or explicitly update the existing stale test import in `tests/unit/test_server_tool_listing.py`, which currently imports a removed `_filter_supported_kwargs` symbol.
6. Keep HTTP proxy documentation and examples aligned with the launcher default of port `8181`.

Acceptance criteria:

- The baseline test collection problem is understood and either repaired or isolated.
- The target GPU host and its API version are recorded in the implementation notes.
- No pre-existing user changes are modified.

## Phase 1: Expand the remote ComfyUI client

Primary files:

- `src/comfy_mcp/comfy_client/client.py`
- `src/comfy_mcp/comfy_client/contracts.py`
- `src/comfy_mcp/comfy_client/errors.py`
- `tests/unit/test_comfy_client.py`

Add explicit client methods for the following read-only endpoints:

- `GET /features`
- `GET /system_stats`
- `GET /api/jobs`
- `GET /api/jobs/{id}`
- `GET /workflow_templates`
- `GET /api/workflow_templates/{pack}/{filename}`
- `GET /global_subgraphs`
- `GET /global_subgraphs/{id}`
- `GET /node_replacements`
- `GET /api/assets`
- `GET /api/assets/{id}`
- `GET /api/tags`

Add explicit client methods for controlled operations:

- interrupt the current execution
- delete or clear queue items
- release model memory
- delete selected or all history
- read and write ComfyUI settings
- upload masks
- upload assets

Implementation requirements:

- Reuse the existing `httpx.AsyncClient` and `ComfyClientError` behavior.
- Centralize request, status, and error normalization.
- Use safe URL encoding for IDs, packs, filenames, and query parameters.
- Add typed contracts only for stable response shapes; retain raw dictionaries for version-sensitive responses.
- Preserve the existing WebSocket URL derivation from `COMFY_MCP_COMFY_BASE_URL`.

Acceptance criteria:

- Every new endpoint has a MockTransport unit test.
- HTTP failures preserve useful ComfyUI error details.
- A remote base URL produces both the correct HTTP and WebSocket targets.

## Phase 2: Add remote introspection tools

Primary files:

- `src/comfy_mcp/mcp_server/tools_comfy.py`
- `src/comfy_mcp/mcp_server/tool_specs.py`
- `src/comfy_mcp/mcp_server/tool_registry.py`
- `tests/unit/test_mcp_tools.py`

Add read-only tools:

- `comfy_capabilities_get`
- `comfy_jobs_list`
- `comfy_job_get`
- `comfy_workflow_templates_list`
- `comfy_workflow_template_get`
- `comfy_global_subgraphs_list`
- `comfy_global_subgraph_get`
- `comfy_node_replacements_get`
- `comfy_assets_list`
- `comfy_asset_get`
- `comfy_tags_list`

Extend `comfy_server_info` or add a dedicated capability audit that reports:

- configured target URL
- DNS and resolved addresses
- queue probe and latency
- ComfyUI feature flags
- system statistics availability
- Manager availability
- available model and embedding discovery
- which mutation gates are active locally

Capability probing should be explicit and cacheable. A failed optional endpoint should be reported as unavailable rather than causing all diagnostics to fail.

Acceptance criteria:

- Read-only tools work when the ComfyUI host has no browser session.
- Tool schemas are available through both stdio MCP and the HTTP proxy.
- Capability results distinguish unreachable, unsupported, and available features.

## Phase 3: Add queue and runtime controls

Primary files:

- `src/comfy_mcp/mcp_server/tools_comfy.py`
- `src/comfy_mcp/mcp_server/tool_specs.py`
- `src/comfy_mcp/mcp_server/policy.py`
- `tests/unit/test_policy.py`
- `tests/unit/test_mcp_tools.py`
- `tests/unit/test_http_server.py`

Add:

- `comfy_queue_interrupt`
- `comfy_queue_delete`
- `comfy_memory_free`
- `comfy_history_delete`
- `comfy_settings_get`
- `comfy_settings_set`

Policy rules:

- Queue inspection and job inspection are read-only.
- Interrupt, queue deletion, memory release, history deletion, and settings writes are mutations.
- Mutations require `readonly_mode=false`.
- Destructive operations require an explicit confirmation field.
- Configured API tokens are enforced through the existing HTTP token injection path.
- Tool responses should identify whether an operation was executed, rejected by policy, or unavailable on the remote ComfyUI version.

Acceptance criteria:

- Readonly mode rejects every new mutation.
- Missing or invalid tokens are rejected when configured.
- Repeated delete or interrupt calls produce stable, understandable responses.

## Phase 4: Add ComfyUI Manager v4 support

Add focused modules:

```text
src/comfy_mcp/comfy_manager/
  client.py
  errors.py
  models.py
src/comfy_mcp/mcp_server/
  tools_manager.py
```

Port and adapt the Manager HTTP client from `ComfyUI_FL-MCP`, with these changes:

- use the configured remote ComfyUI base URL
- detect Manager support through `/features`
- preserve structured connection and API errors
- use short-lived caching for discovery data
- avoid laptop-local Manager package fallbacks
- verify endpoint behavior against the installed remote Manager version

Start with read-only tools:

- Manager status
- installed node packs
- node-to-pack mappings
- snapshots
- external model search
- installed-pack search
- Manager queue status

Then add confirmation-gated mutations:

- install node pack
- update node pack
- uninstall or disable node pack
- install model
- update ComfyUI
- update all

Use a constrained operation enum rather than exposing arbitrary Manager endpoints. Reuse the existing `Policy` and require confirmation for every operation that changes the remote installation.

Acceptance criteria:

- An agent can identify which pack supplies a missing node.
- Read-only Manager discovery works from the laptop with a headless ComfyUI host.
- Mutations are blocked by default and produce a clear confirmation response.
- Manager-unavailable hosts return structured capability results rather than import errors.

## Phase 5: Add filtered node discovery

Primary files:

- `src/comfy_mcp/mcp_server/tools_comfy.py`
- `src/comfy_mcp/mcp_server/tool_specs.py`
- `tests/unit/test_mcp_tools.py`

Add `comfy_nodes_search` over the existing `/object_info` catalog with filters for:

- free-text query
- class type
- category
- input name
- output type
- result limit

Cache the catalog for a short period to avoid repeatedly transferring the full node definition payload.

Acceptance criteria:

- Agents can find a node without receiving the complete catalog.
- Search results preserve enough input/output metadata to plan a workflow.
- Cache invalidation is available after Manager or ComfyUI updates.

## Phase 6: Testing and transport verification

Tests:

- `tests/unit/test_comfy_client.py`: endpoint paths, query encoding, request bodies, WebSocket URL derivation, errors
- `tests/unit/test_mcp_tools.py`: tool behavior against fake clients
- `tests/unit/test_policy.py`: readonly, confirmation, token, and payload behavior
- `tests/unit/test_http_server.py`: token injection, tool schemas, error normalization
- new Manager client and Manager tool tests
- optional live tests guarded by an explicit environment variable

Verification order:

1. focused ComfyClient tests
2. focused tool and policy tests
3. HTTP proxy tests
4. full test suite
5. optional live remote smoke test

The live smoke test should cover:

- capability audit
- queue inspection
- node search
- Manager status
- one known workflow execution
- progress monitoring through the remote WebSocket

## Phase 7: Documentation and rollout

Add or update documentation for:

- laptop-to-GPU topology
- private-network or tunnel requirements
- `COMFY_MCP_COMFY_BASE_URL`
- optional MCP HTTP binding and token configuration
- Manager prerequisites
- readonly and confirmation behavior
- remote input upload and output URL behavior
- unsupported optional ComfyUI endpoints

Recommended rollout order:

1. client endpoint expansion
2. read-only capability and job tools
3. queue interrupt/delete and memory controls
4. Manager read-only discovery
5. Manager mutations
6. node search and documentation polish

Keep browser, filesystem, custom-node coding, git, process restart, and log management out of this integration sequence. Revisit those capabilities as a separate GPU-host administration project.

## Definition of done

- `nfl-comfymcp` can inspect and operate a headless remote ComfyUI through HTTP and WebSocket.
- Agents can diagnose the remote installation, discover nodes and Manager packages, inspect jobs, and control queue state.
- Mutating operations are explicit, policy-gated, and tested.
- The existing workflow catalog, parameter system, lineage system, and Photarium integrations remain intact.
- Focused and full verification results are documented.
- Only the intended files are changed and the pre-existing preference-file modification remains untouched.
