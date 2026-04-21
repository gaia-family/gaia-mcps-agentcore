"""
Deploys the HubSpot MCP Lambda server and wires it to API Gateway.

Steps:
  1. Create Lambda execution role (idempotent)
  2. Create workload identity in AgentCore (idempotent)
  3. Install dependencies + zip source files
  4. Create or update the Lambda function
  5. Create or update the HTTP API Gateway
  6. Save endpoint and role ARN to resulting_config.json

Usage:
    python scripts/06_setup_hubspot_lambda.py
"""

import boto3
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile

from config import save_result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REGION = os.environ["AWS_REGION"]
LAMBDA_NAME = os.environ["HUBSPOT_LAMBDA_NAME"]
ROLE_NAME = os.environ["HUBSPOT_ROLE_NAME"]
API_NAME = os.environ["HUBSPOT_API_NAME"]
WORKLOAD_IDENTITY_NAME = os.environ["HUBSPOT_WORKLOAD_IDENTITY_NAME"]
CREDENTIAL_PROVIDER_NAME = os.environ["HUBSPOT_CREDENTIAL_PROVIDER_NAME"]
SOURCE_DIR = os.path.join(ROOT, "hubspot_mcp_lambda")


def _get_or_create_role(iam) -> str:
    try:
        role = iam.get_role(RoleName=ROLE_NAME)
        print(f"  Using existing role: {ROLE_NAME}")
        return role["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating role '{ROLE_NAME}'...")
    role = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }),
        Description=f"Execution role for {LAMBDA_NAME}",
    )["Role"]

    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="AgentCoreIdentityAccess",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                        "bedrock-agentcore:GetResourceOauth2Token",
                        "bedrock-agentcore:CompleteResourceTokenAuth",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": "arn:aws:secretsmanager:*:*:secret:bedrock-agentcore*",
                },
            ],
        }),
    )

    print("  Waiting 12s for role to propagate...")
    time.sleep(12)
    return role["Arn"]


def _get_or_create_workload_identity(ctrl) -> str:
    try:
        existing = ctrl.get_workload_identity(name=WORKLOAD_IDENTITY_NAME)
        print(f"  Using existing workload identity: {WORKLOAD_IDENTITY_NAME}")
        return existing["name"]
    except ctrl.exceptions.ResourceNotFoundException:
        pass

    print(f"  Creating workload identity '{WORKLOAD_IDENTITY_NAME}'...")
    ctrl.create_workload_identity(
        name=WORKLOAD_IDENTITY_NAME,
        allowedResourceOauth2ReturnUrls=[],
    )
    print(f"  Created: {WORKLOAD_IDENTITY_NAME}")
    return WORKLOAD_IDENTITY_NAME


def _build_zip() -> bytes:
    with tempfile.TemporaryDirectory() as build_dir:
        print(f"  Installing dependencies into {build_dir}...")
        subprocess.check_call([
            "pip", "install",
            "-r", os.path.join(SOURCE_DIR, "requirements.txt"),
            "-t", build_dir,
            "--quiet",
        ])

        for fname in ("server.py", "hubspot_auth.py"):
            shutil.copy(os.path.join(SOURCE_DIR, fname), build_dir)

        zip_path = os.path.join(tempfile.gettempdir(), "hubspot_mcp_lambda.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(build_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    zf.write(full_path, os.path.relpath(full_path, build_dir))

        with open(zip_path, "rb") as f:
            return f.read()


def _get_or_create_lambda(lam, role_arn: str, zip_bytes: bytes) -> str:
    env_vars = {
        "WORKLOAD_IDENTITY_NAME": WORKLOAD_IDENTITY_NAME,
        "CREDENTIAL_PROVIDER_NAME": CREDENTIAL_PROVIDER_NAME,
    }

    try:
        fn = lam.get_function(FunctionName=LAMBDA_NAME)
        fn_arn = fn["Configuration"]["FunctionArn"]
        existing_env = fn["Configuration"].get("Environment", {}).get("Variables", {})
        existing_env.update(env_vars)
        print(f"  Updating existing function...")
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zip_bytes)
        lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Environment={"Variables": existing_env},
        )
        lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
        print(f"  Updated: {fn_arn}")
        return fn_arn
    except lam.exceptions.ResourceNotFoundException:
        pass

    print(f"  Creating function '{LAMBDA_NAME}'...")
    fn = lam.create_function(
        FunctionName=LAMBDA_NAME,
        Runtime="python3.13",
        Role=role_arn,
        Handler="server.lambda_handler",
        Code={"ZipFile": zip_bytes},
        Timeout=30,
        MemorySize=256,
        Environment={"Variables": env_vars},
    )
    lam.get_waiter("function_active_v2").wait(FunctionName=LAMBDA_NAME)
    fn_arn = fn["FunctionArn"]
    print(f"  Created: {fn_arn}")
    return fn_arn


def _get_or_create_api(apigw, lam, fn_arn: str, account_id: str) -> str:
    existing = next(
        (a for a in apigw.get_apis().get("Items", []) if a["Name"] == API_NAME),
        None,
    )

    if existing:
        print(f"  Using existing API: {existing['ApiId']}")
        return f"https://{existing['ApiId']}.execute-api.{REGION}.amazonaws.com/mcp"

    print(f"  Creating API '{API_NAME}'...")
    api = apigw.create_api(
        Name=API_NAME,
        ProtocolType="HTTP",
        CorsConfiguration={
            "AllowOrigins": ["*"],
            "AllowMethods": ["POST", "OPTIONS"],
            "AllowHeaders": ["Content-Type", "Authorization", "Mcp-Protocol-Version"],
        },
    )
    api_id = api["ApiId"]

    lam.add_permission(
        FunctionName=LAMBDA_NAME,
        StatementId="AllowAPIGatewayInvoke",
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=f"arn:aws:execute-api:{REGION}:{account_id}:{api_id}/*",
    )

    integration_id = apigw.create_integration(
        ApiId=api_id,
        IntegrationType="AWS_PROXY",
        IntegrationUri=fn_arn,
        PayloadFormatVersion="2.0",
    )["IntegrationId"]

    apigw.create_route(
        ApiId=api_id,
        RouteKey="POST /mcp",
        Target=f"integrations/{integration_id}",
    )
    apigw.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)

    endpoint = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/mcp"
    print(f"  Created API: {api_id}")
    return endpoint


def main():
    iam = boto3.client("iam")
    lam = boto3.client("lambda", region_name=REGION)
    apigw = boto3.client("apigatewayv2", region_name=REGION)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    print("=== Deploying HubSpot MCP Lambda ===")

    print("\nStep 1: IAM role...")
    role_arn = _get_or_create_role(iam)
    print(f"  Role ARN: {role_arn}")

    print("\nStep 2: Workload identity...")
    _get_or_create_workload_identity(ctrl)

    print("\nStep 3: Building zip...")
    zip_bytes = _build_zip()
    print(f"  Zip size: {len(zip_bytes) / 1024:.0f} KB")

    print("\nStep 4: Lambda function...")
    fn_arn = _get_or_create_lambda(lam, role_arn, zip_bytes)

    print("\nStep 5: API Gateway...")
    endpoint = _get_or_create_api(apigw, lam, fn_arn, account_id)

    save_result({"hubspot_endpoint": endpoint, "hubspot_role_arn": role_arn})

    print(f"\n=== Done ===")
    print(f"Endpoint: {endpoint}")
    print(f"Config saved to resulting_config.json")


if __name__ == "__main__":
    main()
