import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_sheets_auth import get_google_access_token, AuthRequiredError

USER_SUB_HEADER = "x-amzn-bedrock-agentcore-runtime-custom-user-sub"

TOOLS = [
    {
        "name": "debug_context",
        "description": "Returns the user_sub and incoming headers for debugging.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_google_token",
        "description": "Returns the first 7 characters of the Google access token for the current user. Prompts authorization if the user has not connected Google yet.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_spreadsheets",
        "description": "Lists Google Sheets files the current user has access to.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max number of files to return (default 20)."},
            },
            "required": [],
        },
    },
    {
        "name": "get_spreadsheet_info",
        "description": "Returns metadata for a Google Sheets spreadsheet: title and list of sheet names with row/column counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "The Google Sheets spreadsheet ID (from the URL)."},
            },
            "required": ["spreadsheet_id"],
        },
    },
    {
        "name": "get_sheet_values",
        "description": "Returns cell values from a spreadsheet range (e.g. Sheet1!A1:D10).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "The Google Sheets spreadsheet ID."},
                "range": {"type": "string", "description": "A1 notation range, e.g. Sheet1!A1:D10 or just Sheet1."},
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_debug_context(user_sub: str | None, all_headers: dict) -> dict:
    return {
        "user_sub": user_sub,
        "header_keys": list(all_headers.keys()),
    }


def tool_get_google_token(user_sub: str | None, all_headers: dict) -> str:
    try:
        token = get_google_access_token(user_sub)
        return token[:7] + "..."
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except RuntimeError as e:
        return f"Error: {e}"


def _google_credentials(user_sub: str) -> Credentials:
    token = get_google_access_token(user_sub)
    return Credentials(token=token)


def tool_list_spreadsheets(user_sub: str | None, all_headers: dict, limit: int = 20) -> list:
    try:
        creds = _google_credentials(user_sub)
        drive = build("drive", "v3", credentials=creds)
        resp = drive.files().list(
            q="mimeType='application/vnd.google-apps.spreadsheet'",
            pageSize=limit,
            fields="files(id, name, modifiedTime)",
        ).execute()
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "modified_at": f.get("modifiedTime"),
            }
            for f in resp.get("files", [])
        ]
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_spreadsheet_info(user_sub: str | None, all_headers: dict, spreadsheet_id: str) -> dict:
    try:
        creds = _google_credentials(user_sub)
        sheets = build("sheets", "v4", credentials=creds)
        resp = sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="properties.title,sheets.properties",
        ).execute()
        return {
            "title": resp["properties"]["title"],
            "sheets": [
                {
                    "name": s["properties"]["title"],
                    "rows": s["properties"]["gridProperties"].get("rowCount"),
                    "columns": s["properties"]["gridProperties"].get("columnCount"),
                }
                for s in resp.get("sheets", [])
            ],
        }
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_sheet_values(user_sub: str | None, all_headers: dict, spreadsheet_id: str, range: str) -> dict:
    try:
        creds = _google_credentials(user_sub)
        sheets = build("sheets", "v4", credentials=creds)
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range,
        ).execute()
        return {
            "range": resp.get("range"),
            "values": resp.get("values", []),
        }
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "debug_context": tool_debug_context,
    "get_google_token": tool_get_google_token,
    "list_spreadsheets": tool_list_spreadsheets,
    "get_spreadsheet_info": tool_get_spreadsheet_info,
    "get_sheet_values": tool_get_sheet_values,
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
            "serverInfo": {"name": "GoogleSheetsMCPLambda", "version": "1.0.0"},
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
