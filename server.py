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

def cw_get_all(path: str, params: dict = None, max_pages: int = 10) -> list:
    """Fetch pages from a ConnectWise endpoint with a safety cap."""
    params = dict(params or {})
    params["pageSize"] = 1000
    all_results = []
    page = 1
    while True:
        params["page"] = page
        batch = cw_get(path, params)
        if not batch:
            break
        all_results.extend(batch)
        if len(batch) < 1000 or page >= max_pages:
            break
        page += 1
    return all_results

def cw_post(path: str, body: dict):
    r = httpx.post(f"{BASE_URL}{path}", headers=cw_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()

def cw_patch(path: str, operations: list):
    """Send a JSON Patch request (list of {op, path, value} dicts)."""
    r = httpx.patch(f"{BASE_URL}{path}", headers=cw_headers(), json=operations, timeout=30)
    r.raise_for_status()
    return r.json()


# --- MCP Server ---
mcp = FastMCP(
    "ConnectWise",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)


# --- Read Tools ---

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
        "orderBy": "priority/sort asc, dateEntered desc",
        "fields": "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered,_info/lastUpdated",
        "pageSize": page_size,
        "page": 1,
    }
    result = cw_get("/service/tickets", params)
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
        "orderBy": "dateEntered desc",
        "fields": "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered",
        "pageSize": page_size,
        "page": 1,
    }
    result = cw_get("/service/tickets", params)
    return {"count": len(result), "tickets": result}

@mcp.tool()
def get_queue_summary() -> dict:
    """Get a high-level summary of the current ticket queue:
    total open, unassigned count, and breakdown by status, priority, and board."""

    def get_count(conditions: str) -> int:
        r = httpx.get(
            f"{BASE_URL}/service/tickets/count",
            headers=cw_headers(),
            params={"conditions": conditions},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("count", 0)

    total      = get_count("closedFlag=false")
    unassigned = get_count("closedFlag=false and owner/identifier=null")

    # Sample most recent 1000 tickets for breakdown
    sample = cw_get("/service/tickets", {
        "conditions": "closedFlag=false",
        "fields": "status/name,priority/name,board/name,owner/identifier",
        "orderBy": "dateEntered desc",
        "pageSize": 1000,
        "page": 1,
    })

    by_status, by_priority, by_board = {}, {}, {}
    for t in sample:
        s = t.get("status", {}).get("name", "Unknown")
        p = t.get("priority", {}).get("name", "Unknown")
        b = t.get("board", {}).get("name", "Unknown")
        by_status[s]   = by_status.get(s, 0) + 1
        by_priority[p] = by_priority.get(p, 0) + 1
        by_board[b]    = by_board.get(b, 0) + 1

    return {
        "total_open": total,
        "unassigned": unassigned,
        "by_status": by_status,
        "by_priority": by_priority,
        "by_board": by_board,
        "note": f"Breakdown based on most recent 1,000 tickets out of {total} total open.",
    }

@mcp.tool()
def query_tickets(
    conditions: str,
    fields: str = None,
    page_size: int = 100,
) -> dict:
    """Advanced: run a raw ConnectWise API query with custom conditions.
    Use ConnectWise query syntax e.g. \"company/name='Acme' and status/name='New'\"
    This allows answering any question about tickets not covered by the other tools.

    For unassigned tickets, use: owner/identifier=null
    """
    # FIX 1: Use cw_get (single page) instead of cw_get_all (which ignores page_size
    #         and fetches up to 10,000 records regardless).
    # FIX 2: Actually pass page_size into the params (was previously ignored).
    params = {
        "conditions": conditions,
        "orderBy": "dateEntered desc",
        "fields": fields or "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered",
        "pageSize": page_size,  # FIX 2: was missing from params dict
        "page": 1,
    }
    result = cw_get("/service/tickets", params)  # FIX 1: was cw_get_all
    return {"count": len(result), "tickets": result}


if __name__ == "__main__":
    mcp.run(transport="sse")
