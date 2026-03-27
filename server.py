import os
import base64
import httpx
from mcp.server.fastmcp import FastMCP

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

def cw_get_all(path: str, params: dict = None) -> list:
    """Fetch all pages of results from a ConnectWise endpoint."""
    params = dict(params or {})
    params["pageSize"] = 50
    all_results = []
    page = 1
    while True:
        params["page"] = page
        batch = cw_get(path, params)
        if not batch:
            break
        all_results.extend(batch)
        if len(batch) < 50:
            break
        page += 1
    return all_results

# --- MCP Server ---
mcp = FastMCP(
    "ConnectWise",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

@mcp.tool()
def get_open_tickets(
    board: str = None,
    priority: str = None,
    assigned_to: str = None,
    page_size: int = 100,
) -> dict:
    """Get open tickets from the ConnectWise service queue.
    Optionally filter by board name, priority, or assigned member."""
    conditions = ["closedFlag=false"]
    if board:
        conditions.append(f'board/name="{board}"')
    if priority:
        conditions.append(f'priority/name="{priority}"')
    if assigned_to:
        conditions.append(f'owner/identifier="{assigned_to}"')
    params = {
        "conditions": " and ".join(conditions),
        "pageSize": min(page_size, 250),
        "orderBy": "priority/sort asc, dateEntered desc",
        "fields": "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered,_info/lastUpdated"
    }
    result = cw_get_all("/service/tickets", params)
    return {"count": len(result), "tickets": result}


@mcp.tool()
def get_ticket_detail(ticket_id: int) -> dict:
    """Get full details and notes for a specific ticket by ID."""
    ticket = cw_get(f"/service/tickets/{ticket_id}")
    notes  = cw_get(f"/service/tickets/{ticket_id}/notes", {"pageSize": 50})
    return {"ticket": ticket, "notes": notes}


@mcp.tool()
def search_tickets(
    query: str,
    status: str = None,
    company: str = None,
    page_size: int = 100,
) -> dict:
    """Search tickets by keyword in summary. Optionally filter by status or company."""
    conditions = [f'summary contains "{query}"']
    if status:
        conditions.append(f'status/name="{status}"')
    if company:
        conditions.append(f'company/name="{company}"')
    params = {
        "conditions": " and ".join(conditions),
        "pageSize": min(page_size, 250),
        "orderBy": "dateEntered desc",
        "fields": "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered"
    }
    result = cw_get_all("/service/tickets", params)
    return {"count": len(result), "tickets": result}


@mcp.tool()
def get_queue_summary() -> dict:
    """Get a high-level summary of the current ticket queue:
    total open, unassigned count, and breakdown by status, priority, and board."""
    params = {
        "conditions": "closedFlag=false",
        "pageSize": 250,
        "fields": "id,status/name,priority/name,board/name,owner/identifier"
    }
    tickets = cw_get_all("/service/tickets", params)
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
    return {"total_open": len(tickets), "unassigned": unassigned,
            "by_status": by_status, "by_priority": by_priority, "by_board": by_board}


@mcp.tool()
def query_tickets(
    conditions: str,
    fields: str = None,
    page_size: int = 100,
) -> dict:
    """Advanced: run a raw ConnectWise API query with custom conditions.
    Use ConnectWise query syntax e.g. \"company/name='Acme' and status/name='New'\"
    This allows answering any question about tickets not covered by the other tools."""
    params = {
        "conditions": conditions,
        "pageSize": min(page_size, 250),
        "orderBy": "dateEntered desc",
        "fields": fields or "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered"
    }
    result = cw_get_all("/service/tickets", params)
    return {"count": len(result), "tickets": result}


if __name__ == "__main__":
    mcp.run(transport="sse")
