"""
Adds the HubSpot MCP Lambda as a target to the AgentCore Gateway.

The HubSpot Lambda is deployed behind a public API Gateway URL.
The interceptor sets X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Sub on
every request; allowedRequestHeaders ensures the Gateway forwards it.

Reads:
    resulting_config.json  (hubspot_endpoint, gateway_id)
    .env                   (AWS_REGION, GATEWAY_NAME, HUBSPOT_GATEWAY_TARGET)

Usage:
    python scripts/07_add_hubspot_target.py
"""

import boto3
import os
import sys

from config import load_result, save_result

REGION = os.environ["AWS_REGION"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
TARGET_NAME = os.environ["HUBSPOT_GATEWAY_TARGET"]
USER_SUB_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Sub"


def get_gateway_id(ctrl, name: str) -> str:
    for gw in ctrl.list_gateways().get("items", []):
        if name in gw.get("name", ""):
            return gw["gatewayId"]
    raise ValueError(f"Gateway '{name}' not found. Run script 02 first.")


def main():
    cfg = load_result()
    if "hubspot_endpoint" not in cfg:
        print("ERROR: hubspot_endpoint not in resulting_config.json. Run script 06 first.")
        sys.exit(1)

    endpoint = cfg["hubspot_endpoint"]
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"Resolving gateway '{GATEWAY_NAME}'...")
    gateway_id = cfg.get("gateway_id") or get_gateway_id(ctrl, GATEWAY_NAME)
    print(f"  Gateway ID: {gateway_id}")

    print(f"Creating target '{TARGET_NAME}'...")
    print(f"  Endpoint: {endpoint}")

    resp = ctrl.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        description="HubSpot MCP Lambda — per-user OAuth via AgentCore Identity",
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

    save_result({"hubspot_gateway_target_id": target_id})
    print(f"Done. Config saved to resulting_config.json")


if __name__ == "__main__":
    main()
