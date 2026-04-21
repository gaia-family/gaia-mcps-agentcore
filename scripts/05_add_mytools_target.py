"""
Adds the MyTools MCP Runtime as a target to the AgentCore Gateway.

Outbound auth: IAM (GATEWAY_IAM_ROLE + iamCredentialProvider).
The Gateway signs requests to the Runtime using SigV4 with service "bedrock-agentcore".

Reads:
    resulting_config.json  (gateway_id, runtime_arn)
    .env                   (AWS_REGION, GATEWAY_NAME, MYTOOLS_TARGET_NAME, MYTOOLS_RUNTIME_NAME)

Note: Set runtime_arn in resulting_config.json manually after the Runtime has been deployed.

Usage:
    python scripts/05_add_mytools_target.py
"""

import boto3
import os
import sys
import urllib.parse

from config import load_result

REGION = os.environ["AWS_REGION"]
TARGET_NAME = os.environ["MYTOOLS_TARGET_NAME"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
RUNTIME_NAME = os.environ["MYTOOLS_RUNTIME_NAME"]


def get_runtime_endpoint(arn: str, region: str) -> str:
    encoded_arn = urllib.parse.quote(arn, safe="")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
    )


def get_gateway_id(ctrl, gateway_name: str) -> str:
    gateways = ctrl.list_gateways()
    for gw in gateways.get("items", []):
        if gateway_name in gw.get("name", ""):
            return gw["gatewayId"]
    raise ValueError(f"Gateway '{gateway_name}' not found.")


def main():
    cfg = load_result()
    runtime_arn = cfg.get("runtime_arn")
    if not runtime_arn:
        print("ERROR: runtime_arn not found in resulting_config.json.")
        print("Deploy the Runtime first, then set runtime_arn in resulting_config.json.")
        sys.exit(1)

    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"Runtime ARN: {runtime_arn}")
    endpoint = get_runtime_endpoint(runtime_arn, REGION)
    print(f"  Endpoint: {endpoint}")

    print(f"Resolving gateway ID for '{GATEWAY_NAME}'...")
    gateway_id = cfg.get("gateway_id") or get_gateway_id(ctrl, GATEWAY_NAME)
    print(f"  Gateway ID: {gateway_id}")

    existing_targets = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    existing = next((t for t in existing_targets if t.get("name") == TARGET_NAME), None)
    if existing:
        print(f"  Target '{TARGET_NAME}' already exists: {existing['targetId']} — skipping.")
        return

    print(f"Creating gateway target '{TARGET_NAME}'...")
    response = ctrl.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        description="HubSpot MCP Runtime target",
        targetConfiguration={
            "mcp": {
                "mcpServer": {
                    "endpoint": endpoint,
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "GATEWAY_IAM_ROLE",
                "credentialProvider": {
                    "iamCredentialProvider": {
                        "service": "bedrock-agentcore",
                        "region": REGION,
                    }
                },
            }
        ],
        metadataConfiguration={
            "allowedRequestHeaders": ["X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Sub"],
        },
    )

    print(f"  Target ID: {response['targetId']}")
    print(f"Done. Gateway target '{TARGET_NAME}' created successfully.")


if __name__ == "__main__":
    main()
