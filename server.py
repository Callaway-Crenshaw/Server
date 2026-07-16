import os
import base64
import secrets
import time
import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

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


# =====================================================================
# Minimal OAuth shim
#
# This server has no real user accounts, so this shim auto-approves
# every authorization request instead of showing a login screen. It
# exists ONLY to satisfy Claude's OAuth handshake during connector
# setup. It does not add real security beyond what this server already
# has (which, before this shim, was: none). State is in-memory and
# resets on restart -- that's fine for this purpose.
# =====================================================================

_clients = {}
_auth_codes = {}
_tokens = {}


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request):
    base = f"https://{request.headers.get('host', request.url.hostname)}"
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
    })


@mcp.custom_route("/register", methods=["POST"])
async def oauth_register(request: Request):
    body = await request.json()
    client_id = secrets.token_urlsafe(16)
    client_secret = secrets.token_urlsafe(32)
    redirect_uris = body.get("redirect_uris", [])

    _clients[client_id] = {
        "client_secret": client_secret,
        "redirect_uris": redirect_uris,
    }

    return JSONResponse({
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "client_secret_post",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, status_code=201)


@mcp.custom_route("/authorize", methods=["GET"])
async def oauth_authorize(request: Request):
    params = request.query_params
    client_id = params.get("client_id")
    redirect_uri = params.get("redirect_uri")
    state = params.get("state")
    code_challenge = params.get("code_challenge")

    if not client_id or not redirect_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    code = secrets.token_urlsafe(24)
    _auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "created": time.time(),
    }

    redirect_url = f"{redirect_uri}?code={code}"
    if state:
        redirect_url += f"&state={state}"
    return RedirectResponse(url=redirect_url, status_code=302)


@mcp.custom_route("/token", methods=["POST"])
async def oauth_token(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")
    code = form.get("code")

    if grant_type != "authorization_code" or not code or code not in _auth_codes:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    _auth_codes.pop(code)
    access_token = secrets.token_urlsafe(32)
    _tokens[access_token] = {"created": time.time()}

    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    })


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
    params = {
        "conditions": conditions,
        "orderBy": "dateEntered desc",
        "fields": fields or "id,summary,status/name,priority/name,board/name,owner/identifier,company/name,dateEntered",
        "pageSize": page_size,
        "page": 1,
    }
    result = cw_get("/service/tickets", params)
    return {"count": len(result), "tickets": result}


if __name__ == "__main__":
    mcp.run(transport="sse")
