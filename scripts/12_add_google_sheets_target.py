"""
Adds the Google Sheets MCP Lambda as a target to the AgentCore Gateway.

Reads from .env:   GATEWAY_NAME, GOOGLE_SHEETS_GATEWAY_TARGET, AWS_REGION
Reads from resulting_config.json: google_sheets_endpoint
Writes to resulting_config.json: google_sheets_gateway_target_id

Usage:
    python scripts/12_add_google_sheets_target.py
"""

import boto3
import os

import config
from config import load_result, save_result

REGION = os.environ["AWS_REGION"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
TARGET_NAME = os.environ["GOOGLE_SHEETS_GATEWAY_TARGET"]
USER_SUB_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Sub"


def get_gateway_id(ctrl, name: str) -> str:
    for gw in ctrl.list_gateways().get("items", []):
        if name in gw.get("name", ""):
            return gw["gatewayId"]
    raise ValueError(f"Gateway '{name}' not found.")


def main():
    cfg = load_result()
    endpoint = cfg.get("google_sheets_endpoint")
    if not endpoint:
        print("ERROR: google_sheets_endpoint not found in resulting_config.json. Run script 11 first.")
        raise SystemExit(1)

    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"Resolving gateway '{GATEWAY_NAME}'...")
    gateway_id = get_gateway_id(ctrl, GATEWAY_NAME)
    print(f"  Gateway ID: {gateway_id}")

    existing_targets = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    existing = next((t for t in existing_targets if t.get("name") == TARGET_NAME), None)
    if existing:
        target_id = existing["targetId"]
        print(f"  Target '{TARGET_NAME}' already exists: {target_id} — skipping.")
        save_result({"google_sheets_gateway_target_id": target_id})
        return

    print(f"Creating target '{TARGET_NAME}'...")
    print(f"  Endpoint: {endpoint}")

    resp = ctrl.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        description="Google Sheets MCP Lambda — per-user OAuth via AgentCore Identity",
        targetConfiguration={
            "mcp": {
                "mcpServer": {
                    "endpoint": endpoint,
                }
            }
        },
        metadataConfiguration={
            "allowedRequestHeaders": [USER_SUB_HEADER],
        },
    )

    target_id = resp["targetId"]
    print(f"  Target ID: {target_id}")

    save_result({"google_sheets_gateway_target_id": target_id})
    print(f"Done. Target ID saved to resulting_config.json")
    print(f"\nNext: run scripts/13_setup_google_oauth_callback.py")


if __name__ == "__main__":
    main()
