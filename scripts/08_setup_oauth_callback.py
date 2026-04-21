"""
Deploys the OAuth callback Lambda and wires it to API Gateway.

Steps:
  1. Reuse the existing HubSpot MCP Lambda IAM role (already has AgentCore perms)
  2. Zip + deploy the callback Lambda
  3. Create HTTP API Gateway with GET /callback
  4. Update workload identity allowedResourceOauth2ReturnUrls
  5. Set OAUTH_CALLBACK_URL env var on the HubSpot MCP Lambda
  6. Save callback URL to resulting_config.json

Reads:
    resulting_config.json  (hubspot_role_arn)
    .env                   (AWS_REGION, OAUTH_LAMBDA_NAME, OAUTH_API_NAME,
                            HUBSPOT_LAMBDA_NAME, HUBSPOT_WORKLOAD_IDENTITY_NAME)

Usage:
    python scripts/08_setup_oauth_callback.py
"""

import boto3
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from config import load_result, save_result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REGION = os.environ["AWS_REGION"]
CALLBACK_LAMBDA_NAME = os.environ["OAUTH_LAMBDA_NAME"]
API_NAME = os.environ["OAUTH_API_NAME"]
HUBSPOT_LAMBDA_NAME = os.environ["HUBSPOT_LAMBDA_NAME"]
WORKLOAD_IDENTITY_NAME = os.environ["HUBSPOT_WORKLOAD_IDENTITY_NAME"]
SOURCE_DIR = os.path.join(ROOT, "oauth_callback_lambda")


def _build_zip() -> bytes:
    with tempfile.TemporaryDirectory() as build_dir:
        req_file = os.path.join(SOURCE_DIR, "requirements.txt")
        if os.path.exists(req_file):
            print(f"  Installing dependencies into {build_dir}...")
            subprocess.check_call(["pip", "install", "-r", req_file, "-t", build_dir, "--quiet"])
        shutil.copy(os.path.join(SOURCE_DIR, "lambda_function.py"), build_dir)
        zip_path = os.path.join(tempfile.gettempdir(), "oauth_callback.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(build_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    zf.write(full_path, os.path.relpath(full_path, build_dir))
        with open(zip_path, "rb") as f:
            return f.read()


def _get_or_create_lambda(lam, role_arn: str, zip_bytes: bytes) -> str:
    try:
        fn = lam.get_function(FunctionName=CALLBACK_LAMBDA_NAME)
        fn_arn = fn["Configuration"]["FunctionArn"]
        print(f"  Updating existing function...")
        lam.update_function_code(FunctionName=CALLBACK_LAMBDA_NAME, ZipFile=zip_bytes)
        lam.get_waiter("function_updated_v2").wait(FunctionName=CALLBACK_LAMBDA_NAME)
        print(f"  Updated: {fn_arn}")
        return fn_arn
    except lam.exceptions.ResourceNotFoundException:
        pass

    print(f"  Creating function '{CALLBACK_LAMBDA_NAME}'...")
    fn = lam.create_function(
        FunctionName=CALLBACK_LAMBDA_NAME,
        Runtime="python3.13",
        Role=role_arn,
        Handler="lambda_function.lambda_handler",
        Code={"ZipFile": zip_bytes},
        Timeout=15,
        MemorySize=128,
    )
    lam.get_waiter("function_active_v2").wait(FunctionName=CALLBACK_LAMBDA_NAME)
    fn_arn = fn["FunctionArn"]
    print(f"  Created: {fn_arn}")
    return fn_arn


def _get_or_create_api(apigw, lam, fn_arn: str, account_id: str) -> str:
    existing = next(
        (a for a in apigw.get_apis().get("Items", []) if a["Name"] == API_NAME),
        None,
    )
    if existing:
        api_id = existing["ApiId"]
        print(f"  Using existing API: {api_id}")
        return f"https://{api_id}.execute-api.{REGION}.amazonaws.com"

    print(f"  Creating API '{API_NAME}'...")
    api = apigw.create_api(Name=API_NAME, ProtocolType="HTTP")
    api_id = api["ApiId"]

    lam.add_permission(
        FunctionName=CALLBACK_LAMBDA_NAME,
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
        RouteKey="GET /callback",
        Target=f"integrations/{integration_id}",
    )

    apigw.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)
    print(f"  Created API: {api_id}")
    return f"https://{api_id}.execute-api.{REGION}.amazonaws.com"


def _update_workload_identity(ctrl, base_url: str):
    plain = f"{base_url}/callback"
    print(f"  Registering callback URL: {plain}")
    ctrl.update_workload_identity(
        name=WORKLOAD_IDENTITY_NAME,
        allowedResourceOauth2ReturnUrls=[plain],
    )


def _update_hubspot_lambda(lam, callback_url: str):
    cfg = lam.get_function_configuration(FunctionName=HUBSPOT_LAMBDA_NAME)
    env = cfg.get("Environment", {}).get("Variables", {})
    env["OAUTH_CALLBACK_URL"] = callback_url
    lam.update_function_configuration(
        FunctionName=HUBSPOT_LAMBDA_NAME,
        Environment={"Variables": env},
    )
    lam.get_waiter("function_updated_v2").wait(FunctionName=HUBSPOT_LAMBDA_NAME)
    print(f"  Set OAUTH_CALLBACK_URL={callback_url} on {HUBSPOT_LAMBDA_NAME}")


def main():
    cfg = load_result()
    if "hubspot_role_arn" not in cfg:
        print("ERROR: hubspot_role_arn not in resulting_config.json. Run script 06 first.")
        sys.exit(1)

    role_arn = cfg["hubspot_role_arn"]
    print(f"Reusing role: {role_arn}")

    lam = boto3.client("lambda", region_name=REGION)
    apigw = boto3.client("apigatewayv2", region_name=REGION)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    print("\nStep 1: Building zip...")
    zip_bytes = _build_zip()
    print(f"  Zip size: {len(zip_bytes) / 1024:.0f} KB")

    print("\nStep 2: Callback Lambda...")
    fn_arn = _get_or_create_lambda(lam, role_arn, zip_bytes)

    print("\nStep 3: API Gateway...")
    base_url = _get_or_create_api(apigw, lam, fn_arn, account_id)
    callback_url = f"{base_url}/callback"
    print(f"  Callback URL: {callback_url}")

    print("\nStep 4: Updating workload identity...")
    _update_workload_identity(ctrl, base_url)

    print("\nStep 5: Updating HubSpot Lambda env var...")
    _update_hubspot_lambda(lam, callback_url)

    save_result({"oauth_callback_url": callback_url})

    print(f"\n=== Done ===")
    print(f"Callback URL: {callback_url}")
    print(f"Config saved to resulting_config.json")
    print(f"\nNext: invoke the HubSpot tool, visit the authorizationUrl,")
    print(f"then check CloudWatch logs for '{CALLBACK_LAMBDA_NAME}' to see the full event.")


if __name__ == "__main__":
    main()
