import json
import re
from typing import Any, Dict, List, Optional
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
                "folder_id": {"type": "string", "description": "Optional Drive folder ID to search in."},
            },
            "required": [],
        },
    },
    {
        "name": "search_spreadsheets",
        "description": "Search for spreadsheets by name or content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search string."},
                "max_results": {"type": "integer", "description": "Max results (default 20)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_folders",
        "description": "List Google Drive folders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent_folder_id": {"type": "string", "description": "Optional parent folder ID. Lists root folders if omitted."},
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
        "name": "list_sheets",
        "description": "List all sheet/tab names in a spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
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
    {
        "name": "get_sheet_data",
        "description": "Get data from a specific sheet. Optionally include cell formatting metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string", "description": "Sheet name."},
                "range": {"type": "string", "description": "Optional A1 range (e.g. A1:C10)."},
                "include_grid_data": {"type": "boolean", "description": "Include cell formatting metadata (default false)."},
            },
            "required": ["spreadsheet_id", "sheet"],
        },
    },
    {
        "name": "get_sheet_formulas",
        "description": "Get formulas (not computed values) from a sheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string"},
                "range": {"type": "string", "description": "Optional A1 range."},
            },
            "required": ["spreadsheet_id", "sheet"],
        },
    },
    {
        "name": "get_multiple_sheet_data",
        "description": "Get data from multiple ranges across spreadsheets in one call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queries": {"type": "array", "description": "List of objects each with spreadsheet_id, sheet, and range.", "items": {"type": "object"}},
            },
            "required": ["queries"],
        },
    },
    {
        "name": "get_multiple_spreadsheet_summary",
        "description": "Get titles, sheet names, headers, and first rows for multiple spreadsheets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_ids": {"type": "array", "items": {"type": "string"}},
                "rows_to_fetch": {"type": "integer", "description": "Rows per sheet including header (default 5)."},
            },
            "required": ["spreadsheet_ids"],
        },
    },
    {
        "name": "find_in_spreadsheet",
        "description": "Find cells containing a specific value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "query": {"type": "string"},
                "sheet": {"type": "string", "description": "Optional sheet name to restrict search."},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer", "description": "Default 50."},
            },
            "required": ["spreadsheet_id", "query"],
        },
    },
    {
        "name": "create_spreadsheet",
        "description": "Create a new Google Spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "folder_id": {"type": "string", "description": "Optional Drive folder ID."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_sheet",
        "description": "Add a new sheet tab to an existing spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["spreadsheet_id", "title"],
        },
    },
    {
        "name": "update_cells",
        "description": "Update cells in a spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string"},
                "range": {"type": "string", "description": "A1 range (e.g. A1:C3)."},
                "data": {"type": "array", "description": "2D array of values."},
            },
            "required": ["spreadsheet_id", "sheet", "range", "data"],
        },
    },
    {
        "name": "batch_update_cells",
        "description": "Batch update multiple ranges in a spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string"},
                "ranges": {"type": "object", "description": "Dict mapping range strings to 2D arrays."},
            },
            "required": ["spreadsheet_id", "sheet", "ranges"],
        },
    },
    {
        "name": "batch_update",
        "description": "Execute arbitrary batchUpdate requests on a spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "requests": {"type": "array", "description": "List of batchUpdate request objects."},
            },
            "required": ["spreadsheet_id", "requests"],
        },
    },
    {
        "name": "add_rows",
        "description": "Add rows to a sheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string"},
                "count": {"type": "integer"},
                "start_row": {"type": "integer", "description": "0-based row index. Defaults to beginning."},
            },
            "required": ["spreadsheet_id", "sheet", "count"],
        },
    },
    {
        "name": "add_columns",
        "description": "Add columns to a sheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string"},
                "count": {"type": "integer"},
                "start_column": {"type": "integer", "description": "0-based column index. Defaults to beginning."},
            },
            "required": ["spreadsheet_id", "sheet", "count"],
        },
    },
    {
        "name": "copy_sheet",
        "description": "Copy a sheet from one spreadsheet to another.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src_spreadsheet": {"type": "string"},
                "src_sheet": {"type": "string"},
                "dst_spreadsheet": {"type": "string"},
                "dst_sheet": {"type": "string"},
            },
            "required": ["src_spreadsheet", "src_sheet", "dst_spreadsheet", "dst_sheet"],
        },
    },
    {
        "name": "rename_sheet",
        "description": "Rename a sheet tab.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet": {"type": "string"},
                "sheet": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["spreadsheet", "sheet", "new_name"],
        },
    },
    {
        "name": "share_spreadsheet",
        "description": "Share a spreadsheet with users.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "recipients": {"type": "array", "description": "List of objects with email_address and role (reader/commenter/writer)."},
                "send_notification": {"type": "boolean", "description": "Email recipients (default true)."},
            },
            "required": ["spreadsheet_id", "recipients"],
        },
    },
    {
        "name": "add_chart",
        "description": "Add a chart to a spreadsheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet": {"type": "string"},
                "chart_type": {"type": "string", "description": "COLUMN | BAR | LINE | AREA | PIE | SCATTER | COMBO | HISTOGRAM"},
                "data_range": {"type": "string", "description": "A1 range for chart data."},
                "title": {"type": "string"},
                "x_axis_label": {"type": "string"},
                "y_axis_label": {"type": "string"},
                "position_x": {"type": "integer"},
                "position_y": {"type": "integer"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
            "required": ["spreadsheet_id", "sheet", "chart_type", "data_range"],
        },
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _google_credentials(user_sub: str) -> Credentials:
    token = get_google_access_token(user_sub)
    return Credentials(token=token)

def _sheets_svc(user_sub: str):
    return build("sheets", "v4", credentials=_google_credentials(user_sub))

def _drive_svc(user_sub: str):
    return build("drive", "v3", credentials=_google_credentials(user_sub))

def _column_index_to_letter(col_idx: int) -> str:
    result = ""
    n = col_idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result

def _col_letter_to_index(col: str) -> int:
    result = 0
    for c in col.upper():
        result = result * 26 + (ord(c) - ord("A") + 1)
    return result - 1

def _parse_a1_notation(range_str: str) -> dict:
    match = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", range_str.upper())
    if not match:
        raise ValueError(f"Invalid A1 notation: {range_str}")
    return {
        "startRowIndex": int(match.group(2)) - 1,
        "endRowIndex": int(match.group(4)),
        "startColumnIndex": _col_letter_to_index(match.group(1)),
        "endColumnIndex": _col_letter_to_index(match.group(3)) + 1,
    }

def _get_sheet_id(sheets_svc, spreadsheet_id: str, sheet: str) -> Optional[int]:
    spreadsheet = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return next(
        (s["properties"]["sheetId"] for s in spreadsheet["sheets"] if s["properties"]["title"] == sheet),
        None,
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_debug_context(user_sub: str | None, all_headers: dict) -> dict:
    return {"user_sub": user_sub, "header_keys": list(all_headers.keys())}


def tool_get_google_token(user_sub: str | None, all_headers: dict) -> str:
    try:
        return get_google_access_token(user_sub)[:7] + "..."
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except RuntimeError as e:
        return f"Error: {e}"


def tool_list_spreadsheets(user_sub: str | None, all_headers: dict, limit: int = 20, folder_id: str = None) -> list:
    try:
        drive = _drive_svc(user_sub)
        query = "mimeType='application/vnd.google-apps.spreadsheet'"
        if folder_id:
            query += f" and '{folder_id}' in parents"
        results = drive.files().list(
            q=query, spaces="drive", includeItemsFromAllDrives=True,
            supportsAllDrives=True, pageSize=limit,
            fields="files(id, name, modifiedTime)", orderBy="modifiedTime desc",
        ).execute()
        return [{"id": f["id"], "name": f["name"], "modified_at": f.get("modifiedTime")}
                for f in results.get("files", [])]
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_search_spreadsheets(user_sub: str | None, all_headers: dict, query: str, max_results: int = 20) -> list:
    try:
        drive = _drive_svc(user_sub)
        max_results = min(max(1, max_results), 100)
        q = (f"mimeType='application/vnd.google-apps.spreadsheet' and "
             f"(name contains '{query}' or fullText contains '{query}')")
        results = drive.files().list(
            q=q, pageSize=max_results, spaces="drive",
            includeItemsFromAllDrives=True, supportsAllDrives=True,
            fields="files(id, name, createdTime, modifiedTime, owners, webViewLink)",
            orderBy="modifiedTime desc",
        ).execute()
        return [{"id": f["id"], "name": f["name"], "created_time": f.get("createdTime"),
                 "modified_time": f.get("modifiedTime"),
                 "owners": [o.get("emailAddress") for o in f.get("owners", [])],
                 "web_link": f.get("webViewLink")} for f in results.get("files", [])]
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_list_folders(user_sub: str | None, all_headers: dict, parent_folder_id: str = None) -> list:
    try:
        drive = _drive_svc(user_sub)
        query = "mimeType='application/vnd.google-apps.folder'"
        query += f" and '{parent_folder_id}' in parents" if parent_folder_id else " and 'root' in parents"
        results = drive.files().list(
            q=query, spaces="drive", includeItemsFromAllDrives=True,
            supportsAllDrives=True, fields="files(id, name, parents)", orderBy="name",
        ).execute()
        return [{"id": f["id"], "name": f["name"], "parent": (f.get("parents") or ["root"])[0]}
                for f in results.get("files", [])]
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_spreadsheet_info(user_sub: str | None, all_headers: dict, spreadsheet_id: str) -> dict:
    try:
        sheets = build("sheets", "v4", credentials=_google_credentials(user_sub))
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


def tool_list_sheets(user_sub: str | None, all_headers: dict, spreadsheet_id: str) -> list:
    try:
        sheets = _sheets_svc(user_sub)
        spreadsheet = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        return [s["properties"]["title"] for s in spreadsheet["sheets"]]
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_sheet_values(user_sub: str | None, all_headers: dict, spreadsheet_id: str, range: str) -> dict:
    try:
        sheets = build("sheets", "v4", credentials=_google_credentials(user_sub))
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range,
        ).execute()
        return {"range": resp.get("range"), "values": resp.get("values", [])}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_sheet_data(user_sub: str | None, all_headers: dict, spreadsheet_id: str, sheet: str,
                        range: str = None, include_grid_data: bool = False) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        full_range = f"{sheet}!{range}" if range else sheet
        if include_grid_data:
            return sheets.spreadsheets().get(
                spreadsheetId=spreadsheet_id, ranges=[full_range], includeGridData=True,
            ).execute()
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=full_range,
        ).execute()
        return {"spreadsheetId": spreadsheet_id,
                "valueRanges": [{"range": full_range, "values": result.get("values", [])}]}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_sheet_formulas(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                            sheet: str, range: str = None) -> list:
    try:
        sheets = _sheets_svc(user_sub)
        full_range = f"{sheet}!{range}" if range else sheet
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=full_range, valueRenderOption="FORMULA",
        ).execute()
        return result.get("values", [])
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_multiple_sheet_data(user_sub: str | None, all_headers: dict, queries: list) -> list:
    try:
        sheets = _sheets_svc(user_sub)
        results = []
        for q in queries:
            sid, sht, rng = q.get("spreadsheet_id"), q.get("sheet"), q.get("range")
            if not all([sid, sht, rng]):
                results.append({**q, "error": "Missing required keys (spreadsheet_id, sheet, range)"})
                continue
            try:
                result = sheets.spreadsheets().values().get(
                    spreadsheetId=sid, range=f"{sht}!{rng}",
                ).execute()
                results.append({**q, "data": result.get("values", [])})
            except Exception as exc:
                results.append({**q, "error": str(exc)})
        return results
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_get_multiple_spreadsheet_summary(user_sub: str | None, all_headers: dict,
                                          spreadsheet_ids: list, rows_to_fetch: int = 5) -> list:
    try:
        sheets = _sheets_svc(user_sub)
        summaries = []
        for sid in spreadsheet_ids:
            summary: Dict[str, Any] = {"spreadsheet_id": sid, "title": None, "sheets": [], "error": None}
            try:
                spreadsheet = sheets.spreadsheets().get(
                    spreadsheetId=sid,
                    fields="properties.title,sheets(properties(title,sheetId))",
                ).execute()
                summary["title"] = spreadsheet.get("properties", {}).get("title", "Unknown")
                sheet_summaries = []
                for s in spreadsheet.get("sheets", []):
                    title = s.get("properties", {}).get("title")
                    sheet_s: Dict[str, Any] = {"title": title, "sheet_id": s.get("properties", {}).get("sheetId"),
                                               "headers": [], "first_rows": [], "error": None}
                    if not title:
                        sheet_s["error"] = "Sheet title not found"
                        sheet_summaries.append(sheet_s)
                        continue
                    try:
                        max_row = max(1, rows_to_fetch)
                        r = sheets.spreadsheets().values().get(
                            spreadsheetId=sid, range=f"{title}!A1:{max_row}",
                        ).execute()
                        values = r.get("values", [])
                        if values:
                            sheet_s["headers"] = values[0]
                            sheet_s["first_rows"] = values[1:max_row]
                    except Exception as exc:
                        sheet_s["error"] = str(exc)
                    sheet_summaries.append(sheet_s)
                summary["sheets"] = sheet_summaries
            except Exception as exc:
                summary["error"] = str(exc)
            summaries.append(summary)
        return summaries
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_find_in_spreadsheet(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                             query: str, sheet: str = None, case_sensitive: bool = False,
                             max_results: int = 50) -> list:
    try:
        sheets = _sheets_svc(user_sub)
        results: List[Dict[str, Any]] = []
        spreadsheet = sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties(title,sheetId))",
        ).execute()
        sheets_to_search = [
            s["properties"]["title"] for s in spreadsheet.get("sheets", [])
            if sheet is None or s["properties"]["title"] == sheet
        ]
        if not sheets_to_search:
            return [{"error": f"Sheet '{sheet}' not found"}]
        search_query = query if case_sensitive else query.lower()
        for sheet_name in sheets_to_search:
            if len(results) >= max_results:
                break
            response = sheets.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=sheet_name,
            ).execute()
            for row_idx, row in enumerate(response.get("values", [])):
                for col_idx, cell_value in enumerate(row):
                    if len(results) >= max_results:
                        break
                    compare = str(cell_value) if case_sensitive else str(cell_value).lower()
                    if search_query in compare:
                        results.append({"sheet": sheet_name,
                                        "cell": f"{_column_index_to_letter(col_idx)}{row_idx + 1}",
                                        "value": cell_value})
        return results
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_create_spreadsheet(user_sub: str | None, all_headers: dict, title: str, folder_id: str = None) -> dict:
    try:
        drive = _drive_svc(user_sub)
        body: Dict[str, Any] = {"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"}
        if folder_id:
            body["parents"] = [folder_id]
        file = drive.files().create(supportsAllDrives=True, body=body, fields="id, name, parents").execute()
        parents = file.get("parents", [])
        return {"spreadsheetId": file.get("id"), "title": file.get("name", title),
                "folder": parents[0] if parents else "root"}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_create_sheet(user_sub: str | None, all_headers: dict, spreadsheet_id: str, title: str) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        result = sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        props = result["replies"][0]["addSheet"]["properties"]
        return {"sheetId": props["sheetId"], "title": props["title"],
                "index": props.get("index"), "spreadsheetId": spreadsheet_id}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_update_cells(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                      sheet: str, range: str, data: list) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        return sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{sheet}!{range}",
            valueInputOption="USER_ENTERED", body={"values": data},
        ).execute()
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_batch_update_cells(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                            sheet: str, ranges: dict) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        data = [{"range": f"{sheet}!{r}", "values": v} for r, v in ranges.items()]
        return sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_batch_update(user_sub: str | None, all_headers: dict, spreadsheet_id: str, requests: list) -> dict:
    try:
        if not requests:
            return {"error": "requests list cannot be empty"}
        sheets = _sheets_svc(user_sub)
        return sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests},
        ).execute()
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_add_rows(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                  sheet: str, count: int, start_row: int = None) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        sheet_id = _get_sheet_id(sheets, spreadsheet_id, sheet)
        if sheet_id is None:
            return {"error": f"Sheet '{sheet}' not found"}
        idx = start_row if start_row is not None else 0
        return sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"insertDimension": {"range": {
                "sheetId": sheet_id, "dimension": "ROWS",
                "startIndex": idx, "endIndex": idx + count,
            }, "inheritFromBefore": idx > 0}}]},
        ).execute()
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_add_columns(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                     sheet: str, count: int, start_column: int = None) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        sheet_id = _get_sheet_id(sheets, spreadsheet_id, sheet)
        if sheet_id is None:
            return {"error": f"Sheet '{sheet}' not found"}
        idx = start_column if start_column is not None else 0
        return sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"insertDimension": {"range": {
                "sheetId": sheet_id, "dimension": "COLUMNS",
                "startIndex": idx, "endIndex": idx + count,
            }, "inheritFromBefore": idx > 0}}]},
        ).execute()
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_copy_sheet(user_sub: str | None, all_headers: dict, src_spreadsheet: str,
                    src_sheet: str, dst_spreadsheet: str, dst_sheet: str) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        src = sheets.spreadsheets().get(spreadsheetId=src_spreadsheet).execute()
        src_sheet_id = next(
            (s["properties"]["sheetId"] for s in src["sheets"] if s["properties"]["title"] == src_sheet), None,
        )
        if src_sheet_id is None:
            return {"error": f"Source sheet '{src_sheet}' not found"}
        copy_result = sheets.spreadsheets().sheets().copyTo(
            spreadsheetId=src_spreadsheet, sheetId=src_sheet_id,
            body={"destinationSpreadsheetId": dst_spreadsheet},
        ).execute()
        if copy_result.get("title") != dst_sheet:
            rename_result = sheets.spreadsheets().batchUpdate(
                spreadsheetId=dst_spreadsheet,
                body={"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": copy_result["sheetId"], "title": dst_sheet},
                    "fields": "title",
                }}]},
            ).execute()
            return {"copy": copy_result, "rename": rename_result}
        return {"copy": copy_result}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_rename_sheet(user_sub: str | None, all_headers: dict, spreadsheet: str,
                      sheet: str, new_name: str) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        sheet_id = _get_sheet_id(sheets, spreadsheet, sheet)
        if sheet_id is None:
            return {"error": f"Sheet '{sheet}' not found"}
        return sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet,
            body={"requests": [{"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "title": new_name}, "fields": "title",
            }}]},
        ).execute()
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_share_spreadsheet(user_sub: str | None, all_headers: dict, spreadsheet_id: str,
                           recipients: list, send_notification: bool = True) -> dict:
    try:
        drive = _drive_svc(user_sub)
        successes, failures = [], []
        for r in recipients:
            email, role = r.get("email_address"), r.get("role", "writer")
            if not email:
                failures.append({"email_address": None, "error": "Missing email_address"})
                continue
            if role not in ("reader", "commenter", "writer"):
                failures.append({"email_address": email, "error": f"Invalid role '{role}'."})
                continue
            try:
                result = drive.permissions().create(
                    fileId=spreadsheet_id,
                    body={"type": "user", "role": role, "emailAddress": email},
                    sendNotificationEmail=send_notification, fields="id",
                ).execute()
                successes.append({"email_address": email, "role": role, "permissionId": result.get("id")})
            except Exception as exc:
                failures.append({"email_address": email, "error": str(exc)})
        return {"successes": successes, "failures": failures}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


def tool_add_chart(user_sub: str | None, all_headers: dict, spreadsheet_id: str, sheet: str,
                   chart_type: str, data_range: str, title: str = None, x_axis_label: str = None,
                   y_axis_label: str = None, position_x: int = 0, position_y: int = 0,
                   width: int = 600, height: int = 400) -> dict:
    try:
        sheets = _sheets_svc(user_sub)
        valid_types = {"COLUMN", "BAR", "LINE", "AREA", "PIE", "SCATTER", "COMBO", "HISTOGRAM"}
        chart_type = chart_type.upper()
        if chart_type not in valid_types:
            return {"error": f"Invalid chart_type. Must be one of: {', '.join(sorted(valid_types))}"}
        sheet_id = _get_sheet_id(sheets, spreadsheet_id, sheet)
        if sheet_id is None:
            return {"error": f"Sheet '{sheet}' not found"}
        try:
            range_indices = _parse_a1_notation(data_range)
        except ValueError as exc:
            return {"error": str(exc)}
        source_range = {"sheetId": sheet_id, **range_indices}
        if chart_type == "PIE":
            chart_spec: Dict[str, Any] = {"pieChart": {
                "legendPosition": "RIGHT_LEGEND",
                "domain": {"sourceRange": {"sources": [source_range]}},
                "series": {"sourceRange": {"sources": [source_range]}},
            }}
        else:
            axes = [{"position": "BOTTOM_AXIS"}]
            if x_axis_label:
                axes[0]["title"] = x_axis_label
            left_axis: Dict[str, Any] = {"position": "LEFT_AXIS"}
            if y_axis_label:
                left_axis["title"] = y_axis_label
            axes.append(left_axis)
            chart_spec = {"basicChart": {
                "chartType": chart_type, "legendPosition": "RIGHT_LEGEND",
                "axis": axes,
                "domains": [{"domain": {"sourceRange": {"sources": [source_range]}}}],
                "series": [{"series": {"sourceRange": {"sources": [source_range]}}, "targetAxis": "LEFT_AXIS"}],
                "headerCount": 1,
            }}
        if title:
            chart_spec["title"] = title
        result = sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addChart": {"chart": {"spec": chart_spec, "position": {"overlayPosition": {
                "anchorCell": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                "offsetXPixels": position_x, "offsetYPixels": position_y,
                "widthPixels": width, "heightPixels": height,
            }}}}}]},
        ).execute()
        chart_id = result.get("replies", [{}])[0].get("addChart", {}).get("chart", {}).get("chartId")
        return {"success": True, "message": f"Chart '{title or chart_type}' added", "chartId": chart_id}
    except AuthRequiredError as e:
        return f"Google authorization required. Please visit: {e.auth_url}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "debug_context": tool_debug_context,
    "get_google_token": tool_get_google_token,
    "list_spreadsheets": tool_list_spreadsheets,
    "search_spreadsheets": tool_search_spreadsheets,
    "list_folders": tool_list_folders,
    "get_spreadsheet_info": tool_get_spreadsheet_info,
    "list_sheets": tool_list_sheets,
    "get_sheet_values": tool_get_sheet_values,
    "get_sheet_data": tool_get_sheet_data,
    "get_sheet_formulas": tool_get_sheet_formulas,
    "get_multiple_sheet_data": tool_get_multiple_sheet_data,
    "get_multiple_spreadsheet_summary": tool_get_multiple_spreadsheet_summary,
    "find_in_spreadsheet": tool_find_in_spreadsheet,
    "create_spreadsheet": tool_create_spreadsheet,
    "create_sheet": tool_create_sheet,
    "update_cells": tool_update_cells,
    "batch_update_cells": tool_batch_update_cells,
    "batch_update": tool_batch_update,
    "add_rows": tool_add_rows,
    "add_columns": tool_add_columns,
    "copy_sheet": tool_copy_sheet,
    "rename_sheet": tool_rename_sheet,
    "share_spreadsheet": tool_share_spreadsheet,
    "add_chart": tool_add_chart,
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
