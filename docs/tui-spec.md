# Terminal Chat Orchestrator for MCP Tools - Spec

Date: 2026-02-07
Owner: Near Future Laboratory
Status: Draft (for Codex)

## 1) Purpose
Build a terminal-based chat client (TUI) that orchestrates an LLM with multiple MCP servers. The client should:
- Accept natural language requests from the user.
- Allow the LLM to call MCP tools.
- Route tool calls to the correct MCP server (stdio or HTTP).
- Return tool outputs to the LLM and summarize results to the user.

## 2) Scope
### MVP (Must-have)
- TUI with at least two panes: Chat + Tool Activity.
- Tool-calling loop using OpenAI-compatible HTTP.
- Multi-server MCP stdio support (ComfyMCP + Photarium MCP).
- Tool list retrieval and at least one successful tool call.
- Config file for servers, tool prefixes, and LLM settings.

### Nice-to-have (Next)
- Additional panes (Runs, History, Outputs).
- Status polling and progress view.
- Persisted session logs.
- Configurable themes and key bindings.

## 3) Non-goals
- GUI desktop application.
- Training or fine-tuning models.
- Direct image rendering in terminal.

## 4) Architecture Overview
### Components
1) **Chat UI (TUI)**
   - Renders chat transcript and tool activity.
   - Handles user input.

2) **LLM Client**
   - Sends conversation + tool schemas.
   - Receives tool calls and final responses.

3) **MCP Tool Router**
   - Spawns MCP servers as stdio subprocesses.
   - Lists tools per server.
   - Routes tool calls to the correct server.

4) **Tool-Calling Loop**
   - User input -> LLM -> tool calls -> tool results -> LLM -> final response.

### High-level Flow
1) Start TUI.
2) Load config and connect to MCP servers.
3) List tools and expose as tool schema to LLM.
4) For each user message:
   - Send messages + tools to LLM.
   - Execute returned tool calls.
   - Append tool outputs to messages.
   - Continue until final assistant reply.

## 5) Config
### JSON Example
```json
{
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "model": "gpt-4o-mini",
    "temperature": 0.2,
    "timeout_s": 60
  },
  "system_prompt": "You are a terminal chat orchestrator. Decide when to call MCP tools, then summarize results for the user.",
  "servers": [
    {
      "name": "comfy",
      "command": "python",
      "args": ["-m", "comfy_mcp.mcp_server.cli"],
      "tool_prefixes": ["comfy_", "workflows_"]
    },
    {
      "name": "photarium",
      "command": "python",
      "args": ["-m", "photarium_mcp.server"],
      "tool_prefixes": ["photarium_"]
    }
  ]
}
```

### Config Fields
- `llm.base_url`: OpenAI-compatible API base URL.
- `llm.api_key_env`: Env var containing API key.
- `llm.model`: Model name.
- `llm.temperature`: Sampling temperature.
- `llm.timeout_s`: HTTP timeout.
- `servers[]`: MCP server entries.
- `servers[].command`: Executable command.
- `servers[].args`: Command arguments list.
- `servers[].tool_prefixes`: Namespaces for routing.
- `servers[].env` (optional): Environment variables for server.
- `servers[].cwd` (optional): Working directory.

## 6) Data Model
### Message Format
- `system`, `user`, `assistant`, `tool` roles.
- Tool calls use OpenAI tool-call schema.

### Tool Spec
- `name`
- `description`
- `inputSchema`

## 7) Error Handling
- Server start failure: show in chat pane, allow retry.
- Tool call failure: log error to tool pane and pass error as tool output.
- LLM failure: show error in chat pane.

## 8) UX Requirements
- Chat pane shows user and assistant messages.
- Tool pane shows:
  - tool name
  - arguments
  - results or errors
- Input box at bottom.
- Quit with Ctrl+C.

## 9) Testing
- Manual smoke test:
  - Start app.
  - Verify tools listed.
  - Ask for a tool call and ensure output returns.
- Optional unit tests:
  - Router tool map construction.
  - Tool result serialization.
  - LLM tool call parsing.

## 10) Success Criteria
- TUI starts and connects to two MCP servers.
- Tool list shows in tool pane.
- At least one MCP tool is called and output appears in tool pane.
- Final assistant summary appears in chat pane.

## 11) Open Questions
- Which Photarium MCP command should be used in production?
- Should HTTP-based MCP servers be supported in MVP or next phase?
- Should tool prefix routing be strict or allow tool name lookup across all servers?
