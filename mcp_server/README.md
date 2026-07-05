# MCP Server — Action Guardrail

This directory contains a [Model Context Protocol (MCP)](https://modelcontextprotocol.io)
server that exposes the guardrail policy evaluator as a set of tools that any
MCP-compatible client (Claude Desktop, Claude Code, etc.) can call during its
own tool-calling loop.

## Tools

| Tool | Description |
|------|-------------|
| `evaluate_action` | Check whether a proposed tool call is allowed, blocked, or needs human review |
| `list_pending_reviews` | List all pending human-in-the-loop review requests |
| `approve_review` | Approve a pending review, unblocking the action |
| `reject_review` | Reject a pending review, permanently blocking the action |

## Connecting to Claude Desktop

### 1. Install the package

The server must be importable from any directory. Run this once:

```bash
pip install -e .
```

This registers `mcp_server` as a package so `python -m mcp_server.server` works
from anywhere.

### 2. Edit Claude Desktop config

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add an `mcpServers` entry:

```json
{
  "mcpServers": {
    "action-guardrail": {
      "command": "D:\\Project\\action_guardrail\\guardrail\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

Substitute the path to your venv's python.exe. If not using a venv, use
your system python and ensure all dependencies are installed globally.

### 3. Restart Claude Desktop

Set the tool-loading mode to **"Tools already loaded"** (bottom option under
the connectors menu) so the hammer icon appears permanently in the input area.

### 4. Ask Claude

- *"Check if deleting 500 records is allowed by guardrail policy"*
- *"Show me pending guardrail reviews"*
- *"Approve review abc-123 for user Jane"*
- *"Reject review abc-123, reviewer bob"*

Claude calls the MCP tools automatically based on your request.

## Running standalone (stdio)

```bash
python -m mcp_server.server
```

The server listens on stdio and speaks the MCP JSON-RPC protocol. Test it with
any MCP client or the included test script:

```bash
python mcp_server/test_server.py
```

## How it works

The MCP server loads the same policy files and storage backend as the FastAPI
service (reading `STORAGE_BACKEND`, `MONGO_URI`, etc. from `.env`). Decisions
are logged to the same audit log, so MCP-originated evaluations appear in the
dashboard and `/audit-log` endpoint alongside HTTP-originated ones.

Slack notifications and org-scoped multi-tenancy also apply to MCP-originated
evaluations (`SLACK_WEBHOOK_URL` and `ORG_API_KEYS` from `.env`).
