"""
Creates the AgentCore Gateway with Cognito JWT inbound auth.

Steps:
  1. Create the gateway IAM role (trust: bedrock-agentcore.amazonaws.com)
  2. Create or retrieve the Gateway (idempotent)
  3. Save gateway_id and role_arn to resulting_config.json

Reads:
    resulting_config.json  (cognito_discovery_url, cognito_user_client_id, cognito_agent_client_id)
    .env                   (AWS_REGION, GATEWAY_NAME, GATEWAY_ROLE_NAME)

Usage:
    python scripts/02_create_gateway.py
"""

import boto3
import json
import os
import sys
import time

from config import load_result, save_result

REGION = os.environ["AWS_REGION"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
GATEWAY_ROLE_NAME = os.environ["GATEWAY_ROLE_NAME"]


def get_or_create_gateway_role(iam) -> str:
    try:
        role = iam.get_role(RoleName=GATEWAY_ROLE_NAME)["Role"]
        print(f"  Using existing role: {role['Arn']}")
        return role["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating role '{GATEWAY_ROLE_NAME}'...")
    role = iam.create_role(
        RoleName=GATEWAY_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }),
        Description=f"Execution role for {GATEWAY_NAME} AgentCore Gateway",
    )["Role"]

    iam.attach_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
    )

    print("  Waiting 12s for role to propagate...")
    time.sleep(12)
    return role["Arn"]


def get_or_create_gateway(ctrl, role_arn: str, discovery_url: str, allowed_clients: list[str]) -> str:
    existing = next(
        (gw for gw in ctrl.list_gateways().get("items", []) if gw.get("name") == GATEWAY_NAME),
        None,
    )

    if existing:
        gateway_id = existing["gatewayId"]
        print(f"  Gateway already exists: {gateway_id}")
        return gateway_id

    print(f"  Creating gateway '{GATEWAY_NAME}'...")
    resp = ctrl.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJwtAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": allowed_clients,
            }
        },
    )
    gateway_id = resp["gatewayId"]
    print(f"  Created: {gateway_id}")
    return gateway_id


def main():
    cfg = load_result()
    if "cognito_discovery_url" not in cfg:
        print("ERROR: cognito_discovery_url not in resulting_config.json. Run script 01 first.")
        sys.exit(1)

    discovery_url = cfg["cognito_discovery_url"]
    allowed_clients = [cfg["cognito_user_client_id"], cfg["cognito_agent_client_id"]]

    iam = boto3.client("iam")
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print("Step 1: Gateway IAM role")
    role_arn = get_or_create_gateway_role(iam)
    print(f"  Role ARN: {role_arn}")

    print("\nStep 2: Gateway")
    print(f"  Discovery URL:    {discovery_url}")
    print(f"  Allowed clients:  {allowed_clients}")
    gateway_id = get_or_create_gateway(ctrl, role_arn, discovery_url, allowed_clients)

    save_result({"gateway_id": gateway_id, "gateway_role_arn": role_arn})

    print(f"\nDone. Config saved to resulting_config.json")
    print(f"  Gateway ID: {gateway_id}")
    print(f"\nNext: run scripts/03_setup_interceptor.py")


if __name__ == "__main__":
    main()
