import json
from hubspot import HubSpot
from hubspot_auth import get_hubspot_access_token, AuthRequiredError

USER_SUB_HEADER = "x-amzn-bedrock-agentcore-runtime-custom-user-sub"

TOOLS = [
    {
        "name": "debug_context",
        "description": "Returns the user_sub and incoming headers for debugging.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_hubspot_token",
        "description": "Returns the first 7 characters of the HubSpot access token for the current user. Prompts authorization if the user has not connected HubSpot yet.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_contacts",
        "description": "Returns a list of HubSpot contacts (firstname, lastname, email).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max number of contacts to return (default 10)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_contact",
        "description": "Returns a single HubSpot contact by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "The HubSpot contact ID."},
            },
            "required": ["contact_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# user_sub is extracted from headers by the Lambda handler and passed in here.
# It is never a tool parameter — Claude never sees it.
# ---------------------------------------------------------------------------

def tool_debug_context(user_sub: str | None, all_headers: dict) -> dict:
    return {
        "user_sub": user_sub,
        "header_keys": list(all_headers.keys()),
    }


def tool_get_hubspot_token(user_sub: str | None, all_headers: dict) -> str:
    try:
        token = get_hubspot_access_token(user_sub)
        return token[:7] + "..."
    except AuthRequiredError as e:
        return f"HubSpot authorization required. Please visit: {e.auth_url}"
    except RuntimeError as e:
        return f"Error: {e}"


def _hubspot_client(user_sub: str) -> HubSpot:
    token = get_hubspot_access_token(user_sub)
    return HubSpot(access_token=token)


def tool_get_contacts(user_sub: str | None, all_headers: dict, limit: int = 10) -> list:
    try:
        client = _hubspot_client(user_sub)
        props = ["firstname", "lastname", "email"]
        response = client.crm.contacts.basic_api.get_page(limit=limit, properties=props)
        return [
            {
                "id": c.id,
                "firstname": c.properties.get("firstname"),
                "lastname": c.properties.get("lastname"),
                "email": c.properties.get("email"),
            }
            for c in response.results
        ]
    except AuthRequiredError as e:
        return f"HubSpot authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_contact(user_sub: str | None, all_headers: dict, contact_id: str) -> dict:
    try:
        client = _hubspot_client(user_sub)
        props = ["firstname", "lastname", "email", "phone", "company"]
        c = client.crm.contacts.basic_api.get_by_id(contact_id, properties=props)
        return {
            "id": c.id,
            "firstname": c.properties.get("firstname"),
            "lastname": c.properties.get("lastname"),
            "email": c.properties.get("email"),
            "phone": c.properties.get("phone"),
            "company": c.properties.get("company"),
        }
    except AuthRequiredError as e:
        return f"HubSpot authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "debug_context": tool_debug_context,
    "get_hubspot_token": tool_get_hubspot_token,
    "get_contacts": tool_get_contacts,
    "get_contact": tool_get_contact,
}


# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatcher
# ---------------------------------------------------------------------------

def handle_request(body: dict, user_sub: str | None, all_headers: dict) -> dict | None:
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "HubSpotMCPLambda", "version": "1.0.0"},
        }

    elif method == "tools/list":
        result = {"tools": TOOLS}

    elif method == "tools/call":
        name = params.get("name")
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                "id": req_id,
            }
        tool_args = params.get("arguments") or {}
        output = handler(user_sub, all_headers, **tool_args)
        text = output if isinstance(output, str) else json.dumps(output)
        result = {"content": [{"type": "text", "text": text}]}

    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None

    else:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id,
        }

    return {"jsonrpc": "2.0", "result": result, "id": req_id}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    try:
        all_headers = event.get("headers") or {}
        user_sub = all_headers.get(USER_SUB_HEADER)

        body = json.loads(event.get("body") or "{}")

        if isinstance(body, list):
            responses = [
                r for r in [handle_request(b, user_sub, all_headers) for b in body]
                if r is not None
            ]
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(responses),
            }

        response = handle_request(body, user_sub, all_headers)
        if response is None:
            return {"statusCode": 202, "body": ""}
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response),
        }

    except Exception as e:
        import traceback
        print("UNHANDLED ERROR:", traceback.format_exc())
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
