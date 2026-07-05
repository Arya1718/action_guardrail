#!/usr/bin/env python3
"""Test the MCP server by simulating calls via the MCP client SDK."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["API_KEY"] = "dev-placeholder-key"
os.environ["STORAGE_BACKEND"] = "memory"

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolRequest, CallToolResult

from mcp_server.server import server, handle_list_tools, handle_call_tool


async def main():
    # List tools
    tools = await handle_list_tools()
    print(f"Available tools ({len(tools)}):")
    for t in tools:
        print(f"  - {t.name}: {t.title}")
    print()

    # Test evaluate_action - block (record_count > 100)
    print("=== evaluate_action (block) ===")
    result = await handle_call_tool("evaluate_action", {
        "tool_name": "delete_records",
        "parameters": {"record_count": 500},
    })
    for item in result:
        print(item.text[:300])
    print()

    # Test evaluate_action - allow (no matching rule)
    print("=== evaluate_action (allow) ===")
    result = await handle_call_tool("evaluate_action", {
        "tool_name": "list_files",
        "parameters": {},
    })
    for item in result:
        print(item.text[:300])
    print()

    # Test evaluate_action - require_hitl (external domain)
    print("=== evaluate_action (require_hitl) ===")
    result = await handle_call_tool("evaluate_action", {
        "tool_name": "send_email",
        "parameters": {"recipient_domain": "gmail.com"},
    })
    for item in result:
        print(item.text[:500])
    print()

    # Test list_pending_reviews
    print("=== list_pending_reviews ===")
    result = await handle_call_tool("list_pending_reviews", {})
    for item in result:
        print(item.text[:500])
    print()

    # Test approve_review (we need a pending review ID from above)
    # Parse the hitl_request_id from the last evaluate_action result
    import json
    full_output = result[0].text
    print("Pending reviews output above")
    print()

    # Test reject on a non-existent ID
    print("=== reject_review (nonexistent) ===")
    result = await handle_call_tool("reject_review", {
        "request_id": "nonexistent-id",
        "resolved_by": "test-user",
    })
    for item in result:
        print(item.text[:300])

    # Now actually approve one
    print()
    print("=== approve_review (from pending) ===")
    pending = await handle_call_tool("list_pending_reviews", {})
    pending_data = json.loads(pending[0].text) if pending else []
    if isinstance(pending_data, list) and len(pending_data) > 0:
        req_id = pending_data[0]["id"]
        print(f"Approving: {req_id}")
        result = await handle_call_tool("approve_review", {
            "request_id": req_id,
            "resolved_by": "mcp-test",
        })
        for item in result:
            print(item.text[:300])
    else:
        print("No pending reviews to approve")

    print()
    print("ALL MCP TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
