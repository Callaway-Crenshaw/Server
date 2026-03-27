import os
import base64
import httpx
import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount
import json

# --- Config from environment variables ---
CW_SITE        = os.environ["CW_SITE"]
CW_COMPANY_ID  = os.environ["CW_COMPANY_ID"]
CW_PUBLIC_KEY  = os.environ["CW_PUBLIC_KEY"]
CW_PRIVATE_KEY = os.environ["CW_PRIVATE_KEY"]
CW_CLIENT_ID   = os.environ["CW_CLIENT_ID"]

BASE_URL = f"https://{CW_SITE}/v4_6_release/apis/3.0"

def cw_headers():
    token = base64.b64encode(f"{CW_COMPANY_ID}+{CW_PUBLIC_KEY}:{CW_PRIVATE_KEY}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "clientId": CW_CLIENT_ID,
        "Content-Type": "application/json",
    }

def cw_get(path: str, params: dict = None):
    r = httpx.get(f"{BASE_URL}{path}", headers=cw_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

# --- MCP Server ---
server = Server("connectwise")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_open_tickets",
            description="Get open tickets from the ConnectWise service queue. Filter by board, priority, or assigned member.",
            inputSchema={
                "type": "object",
                "properties": {
                    "board":       {"type": "string", "description": "Board name to filter by"},
                    "priority":    {"type": "string", "description": "Priority name e.g. 'Priority 1'"},
                    "assigned_to": {"type": "string", "description": "Member identifier to filter by"},
                    "page_size":   {"type": "integer", "description": "Max tickets to return (default 25)"}
                }
            }
        ),
        Tool(
            name="get_ticket_detail",
            description="Get full details and notes for a specific ticket by ID.",
            inputSchema={
                "type": "object",
                "required": ["ticket_id"],
                "properties": {
                    "ticket_id": {"type": "integer", "description": "The ConnectWise ticket ID"}
                }
            }
        ),
        Tool(
            name="search_tickets",
            description="Search tickets by keyword in summary. Optionally filter by status or company.",
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query":     {"type": "string", "description": "Keyword to search in ticket summaries"},
                    "status":    {"type": "string", "description": "Status name to filter by"},
                    "company":   {"type": "string", "description": "Company name to filter by"},
                    "page_size": {"type": "integer", "description": "Max tickets to return (default 25)"}
                }
            }
        ),
        Tool(
            name="get_queue_summary",
            description="Get a high-level summary of the current ticket queue: counts by status, priority, board, and unassigned count.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="query_tickets",
            description="Advanced: run a raw ConnectWise API query with custom conditions. Use ConnectWise query syntax e.g. \"company/name='Acme' and status/name='New'\"",
            inputSchema={
                "type": "object",
                "required": ["conditions"],
                "properties": {
                    "conditions": {"type": "string", "description": "ConnectWise API condition string"},
                    "fields":     {"type": "string", "description": "Comma-separated fields to return"},
                    "page_size":  {"type": "integer", "description": "Max tickets to return (default 25)"}
                }
            }
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "get_open_tickets":
            conditions = ["status/name!='Closed'", "status/name!='Completed'"]
            if arguments.get("board"):
                conditions.append(f'board/name="{arguments["board"]}"')
            if arguments.get("priority"):
                conditions.append(f'priority/name="{arguments["priority"]}"')
            if arguments.get("assigned_to"):
                conditions.append(f'owner/identifier="{arguments["assigned_to"]}"')
            params = {
                "conditions": " and ".join(conditions),
                "pageSize": min(arguments.get("page_size", 25), 100),
                "orderBy": "priority/sort asc, dateEntered desc",
                "fields": "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered,_info/lastUpdated"
            }
            result = cw_get("/service/tickets", params)
            return [TextContent(type="text", text=json.dumps({"count": len(result), "tickets": result}))]

        elif name == "get_ticket_detail":
            ticket = cw_get(f"/service/tickets/{arguments['ticket_id']}")
            notes  = cw_get(f"/service/tickets/{arguments['ticket_id']}/notes", {"pageSize": 50})
            return [TextContent(type="text", text=json.dumps({"ticket": ticket, "notes": notes}))]

        elif name == "search_tickets":
            conditions = [f'summary contains "{arguments["query"]}"']
            if arguments.get("status"):
                conditions.append(f'status/name="{arguments["status"]}"')
            if arguments.get("company"):
                conditions.append(f'company/name="{arguments["company"]}"')
            params = {
                "conditions": " and ".join(conditions),
                "pageSize": min(arguments.get("page_size", 25), 100),
                "orderBy": "dateEntered desc",
                "fields": "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered"
            }
            result = cw_get("/service/tickets", params)
            return [TextContent(type="text", text=json.dumps({"count": len(result), "tickets": result}))]

        elif name == "get_queue_summary":
            params = {
                "conditions": "status/name!='Closed' and status/name!='Completed'",
                "pageSize": 1000,
                "fields": "id,status/name,priority/name,board/name,owner/identifier"
            }
            tickets = cw_get("/service/tickets", params)
            by_status, by_priority, by_board, unassigned = {}, {}, {}, 0
            for t in tickets:
                s = t.get("status", {}).get("name", "Unknown")
                p = t.get("priority", {}).get("name", "Unknown")
                b = t.get("board", {}).get("name", "Unknown")
                by_status[s]   = by_status.get(s, 0) + 1
                by_priority[p] = by_priority.get(p, 0) + 1
                by_board[b]    = by_board.get(b, 0) + 1
                if not t.get("owner"):
                    unassigned += 1
            result = {"total_open": len(tickets), "unassigned": unassigned,
                      "by_status": by_status, "by_priority": by_priority, "by_board": by_board}
            return [TextContent(type="text", text=json.dumps(result))]

        elif name == "query_tickets":
            params = {
                "conditions": arguments["conditions"],
                "pageSize": min(arguments.get("page_size", 25), 100),
                "orderBy": "dateEntered desc",
                "fields": arguments.get("fields") or "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered"
            }
            result = cw_get("/service/tickets", params)
            return [TextContent(type="text", text=json.dumps({"count": len(result), "tickets": result}))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# --- Starlette app with explicit /sse route ---
sse = SseServerTransport("/messages")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Mount("/messages", app=sse.handle_post_message),
])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
