"""
Lists all tools available on the Gateway via MCP tools/list.
Prints the full raw JSON response.

Usage:
    python scripts/list_tools.py
"""

import boto3
import json
import os
import requests

from config import load_result

REGION = os.environ["AWS_REGION"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]


def main():
    cfg = load_result()
    if "cognito_username" not in cfg:
        print("ERROR: cognito_username not in resulting_config.json. Run script 01 first.")
        return

    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth_result = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": cfg["cognito_username"],
            "PASSWORD": cfg["cognito_password"],
        },
        ClientId=cfg["cognito_user_client_id"],
    )
    token = auth_result["AuthenticationResult"]["AccessToken"]
    print(f"Token obtained (first 20 chars): {token[:20]}...\n")

    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gateway_url = None
    for gw in ctrl.list_gateways().get("items", []):
        if GATEWAY_NAME in gw["name"]:
            gateway_url = ctrl.get_gateway(gatewayIdentifier=gw["gatewayId"])["gatewayUrl"]
            break
    print(f"Gateway URL: {gateway_url}\n")

    resp = requests.post(
        gateway_url,
        json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    print("Raw response:")
    print(json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    main()
