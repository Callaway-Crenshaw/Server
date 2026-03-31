# -------------------------------------------------------
# 1. Add these helper functions after your existing cw_get / cw_get_all
# -------------------------------------------------------

def cw_post(path: str, body: dict):
    r = httpx.post(f"{BASE_URL}{path}", headers=cw_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()

def cw_patch(path: str, operations: list):
    """Send a JSON Patch request (list of {op, path, value} dicts)."""
    r = httpx.patch(f"{BASE_URL}{path}", headers=cw_headers(), json=operations, timeout=30)
    r.raise_for_status()
    return r.json()


# -------------------------------------------------------
# 2. Add these tools after your existing @mcp.tool() definitions
# -------------------------------------------------------

@mcp.tool()
def create_ticket(
    summary: str,
    board_name: str,
    company_name: str,
    status_name: str = "New",
    priority_name: str = "Priority 3 - Normal Response",
    assigned_to: str = None,
    initial_description: str = None,
    type_name: str = None,
    subtype_name: str = None,
    item_name: str = None,
) -> dict:
    """Create a new service ticket in ConnectWise Manage.
    Returns the created ticket object including its new ID."""
    body = {
        "summary": summary,
        "board": {"name": board_name},
        "company": {"identifier": company_name},
        "status": {"name": status_name},
        "priority": {"name": priority_name},
    }
    if assigned_to:
        body["owner"] = {"identifier": assigned_to}
    if initial_description:
        body["initialDescription"] = initial_description
    if type_name:
        body["type"] = {"name": type_name}
    if subtype_name:
        body["subType"] = {"name": subtype_name}
    if item_name:
        body["item"] = {"name": item_name}

    return cw_post("/service/tickets", body)


@mcp.tool()
def create_ticket_note(
    ticket_id: int,
    text: str,
    internal: bool = True,
) -> dict:
    """Add a note to an existing ticket.
    Set internal=False to make it a customer-visible note."""
    body = {
        "text": text,
        "internalAnalysisFlag": internal,
        "detailDescriptionFlag": False,
        "resolutionFlag": False,
    }
    return cw_post(f"/service/tickets/{ticket_id}/notes", body)


@mcp.tool()
def update_ticket(
    ticket_id: int,
    summary: str = None,
    status_name: str = None,
    priority_name: str = None,
    assigned_to: str = None,
) -> dict:
    """Update fields on an existing ticket using JSON Patch.
    Only provide the fields you want to change."""
    ops = []
    if summary:
        ops.append({"op": "replace", "path": "/summary", "value": summary})
    if status_name:
        ops.append({"op": "replace", "path": "/status/name", "value": status_name})
    if priority_name:
        ops.append({"op": "replace", "path": "/priority/name", "value": priority_name})
    if assigned_to:
        ops.append({"op": "replace", "path": "/owner/identifier", "value": assigned_to})
    if not ops:
        return {"error": "No fields provided to update."}
    return cw_patch(f"/service/tickets/{ticket_id}", ops)
