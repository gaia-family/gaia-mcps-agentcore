"""
Deploys the Gateway Request Interceptor Lambda and wires it to the Gateway.

The AgentCore CLI does not support interceptor configuration in agentcore.json,
so this script is the single source of truth for the interceptor. Re-run it
whenever the interceptor code changes or after a full Gateway redeploy.

  1. Create Lambda execution role (idempotent)
  2. Zip interceptor/lambda_function.py and create/update the Lambda function
  3. Look up the Gateway ID and its IAM role ARN
  4. Grant the Gateway role lambda:InvokeFunction on the Lambda (scoped, not wildcard)
  5. Allowlist X-User-Sub on the Runtime target (reads existing config, merges safely)
  6. Call update_gateway to wire the interceptor

Usage:
    python scripts/03_setup_interceptor.py
"""

import boto3
import io
import json
import os
import time
import zipfile

import config  # loads .env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REGION = os.environ["AWS_REGION"]
GATEWAY_NAME = os.environ["GATEWAY_NAME"]
TARGET_NAME = os.environ["MYTOOLS_TARGET_NAME"]
LAMBDA_NAME = os.environ["INTERCEPTOR_LAMBDA_NAME"]
LAMBDA_ROLE_NAME = os.environ["INTERCEPTOR_ROLE_NAME"]
INTERCEPTOR_SOURCE = os.path.join(ROOT, "interceptor", "lambda_function.py")


def _zip_lambda() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(INTERCEPTOR_SOURCE, "lambda_function.py")
    return buf.getvalue()


def _get_or_create_lambda_role(iam) -> str:
    trust = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })
    try:
        role = iam.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]
        print(f"  Lambda role already exists: {role['Arn']}")
        return role["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating role '{LAMBDA_ROLE_NAME}'...")
    role = iam.create_role(
        RoleName=LAMBDA_ROLE_NAME,
        AssumeRolePolicyDocument=trust,
        Description=f"Execution role for {LAMBDA_ROLE_NAME}",
    )["Role"]
    iam.attach_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    # IAM is eventually consistent — brief wait prevents Lambda creation race
    print("  Waiting 12 s for role to propagate...")
    time.sleep(12)
    return role["Arn"]


def _get_or_create_lambda(lam, role_arn: str) -> str:
    zip_bytes = _zip_lambda()
    try:
        fn = lam.get_function(FunctionName=LAMBDA_NAME)
        fn_arn = fn["Configuration"]["FunctionArn"]
        print(f"  Function already exists, updating code...")
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zip_bytes)
        waiter = lam.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=LAMBDA_NAME)
        print(f"  Code updated: {fn_arn}")
        return fn_arn
    except lam.exceptions.ResourceNotFoundException:
        pass

    print(f"  Creating function '{LAMBDA_NAME}'...")
    fn = lam.create_function(
        FunctionName=LAMBDA_NAME,
        Runtime="python3.13",
        Role=role_arn,
        Handler="lambda_function.lambda_handler",
        Code={"ZipFile": zip_bytes},
        Description=(
            "Gateway interceptor: extracts Cognito sub from inbound JWT "
            "and forwards it as X-User-Sub to the Runtime"
        ),
        Timeout=10,
        MemorySize=128,
    )
    fn_arn = fn["FunctionArn"]
    waiter = lam.get_waiter("function_active_v2")
    waiter.wait(FunctionName=LAMBDA_NAME)
    print(f"  Created: {fn_arn}")
    return fn_arn


def _get_gateway(ctrl) -> dict:
    for gw in ctrl.list_gateways().get("items", []):
        if GATEWAY_NAME in gw.get("name", ""):
            return ctrl.get_gateway(gatewayIdentifier=gw["gatewayId"])
    raise ValueError(f"Gateway '{GATEWAY_NAME}' not found.")


def _grant_gateway_invoke(iam, gateway_role_arn: str, lambda_arn: str) -> None:
    role_name = gateway_role_arn.split("/")[-1]
    policy_name = "InvokeInterceptorLambda"
    policy_doc = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowInvokeInterceptorLambda",
            "Effect": "Allow",
            "Action": "lambda:InvokeFunction",
            "Resource": lambda_arn,
        }],
    })
    print(f"  Attaching inline policy '{policy_name}' to role '{role_name}'...")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=policy_doc,
    )


CUSTOM_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Sub"
LEGACY_HEADER = "X-User-Sub"  # old name — removed on re-run


def _allowlist_header_on_target(ctrl, gateway_id: str, target_summary: dict) -> None:
    target_id = target_summary["targetId"]
    target_name = target_summary["name"]

    existing = ctrl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)

    existing_metadata = existing.get("metadataConfiguration") or {}
    existing_headers = existing_metadata.get("allowedRequestHeaders") or []

    updated_headers = [h for h in existing_headers if h != LEGACY_HEADER]
    if CUSTOM_HEADER not in updated_headers:
        updated_headers.append(CUSTOM_HEADER)

    if updated_headers == existing_headers:
        print(f"  '{target_name}': already correct — skipping.")
        return

    print(f"  '{target_name}' ({target_id}): {existing_headers} → {updated_headers}")
    kwargs = dict(
        gatewayIdentifier=gateway_id,
        targetId=target_id,
        name=existing["name"],
        targetConfiguration=existing["targetConfiguration"],
        metadataConfiguration={
            **existing_metadata,
            "allowedRequestHeaders": updated_headers,
        },
    )
    if "credentialProviderConfigurations" in existing:
        kwargs["credentialProviderConfigurations"] = existing["credentialProviderConfigurations"]
    ctrl.update_gateway_target(**kwargs)


def _allowlist_user_sub(ctrl, gateway_id: str) -> None:
    targets = ctrl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    for t in targets:
        _allowlist_header_on_target(ctrl, gateway_id, t)


def _print_wiring_instructions(gateway_id: str, lambda_arn: str) -> None:
    print()
    print("  Manual step — add the interceptor in the AWS Console:")
    print(f"  1. Open AgentCore > Gateways > {GATEWAY_NAME}")
    print(f"  2. Edit the Gateway, find 'Interceptors'")
    print(f"  3. Add interceptor:")
    print(f"       Lambda ARN:          {lambda_arn}")
    print(f"       Interception point:  REQUEST")
    print(f"       Pass request headers: true")
    print()
    print("  Or via boto3 (fill in the other required fields from get_gateway first):")
    print(f"    ctrl.update_gateway(")
    print(f"        gatewayIdentifier='{gateway_id}',")
    print(f"        # ... name, roleArn, protocolType, authorizerType (copy from get_gateway)")
    print(f"        interceptorConfigurations=[{{")
    print(f"            'interceptor': {{'lambda': {{'arn': '{lambda_arn}'}}}},")
    print(f"            'interceptionPoints': ['REQUEST'],")
    print(f"            'inputConfiguration': {{'passRequestHeaders': True}},")
    print(f"        }}],")
    print(f"    )")


def main():
    iam = boto3.client("iam")
    lam = boto3.client("lambda", region_name=REGION)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print("Step 1: Lambda execution role")
    role_arn = _get_or_create_lambda_role(iam)
    print(f"  Role ARN: {role_arn}")

    print("\nStep 2: Lambda function")
    lambda_arn = _get_or_create_lambda(lam, role_arn)
    print(f"  Lambda ARN: {lambda_arn}")

    print("\nStep 3: Locate Gateway")
    gateway = _get_gateway(ctrl)
    gateway_id = gateway["gatewayId"]
    gateway_role_arn = gateway["roleArn"]
    print(f"  Gateway ID:       {gateway_id}")
    print(f"  Gateway role ARN: {gateway_role_arn}")

    print("\nStep 4: Grant Gateway role lambda:InvokeFunction")
    _grant_gateway_invoke(iam, gateway_role_arn, lambda_arn)
    print("  Done.")

    print("\nStep 5: Allowlist X-User-Sub on target")
    _allowlist_user_sub(ctrl, gateway_id)
    print("  Done.")

    print(f"\nLambda deployed. Lambda ARN: {lambda_arn}")
    print("\nStep 6: Wire interceptor to Gateway")
    _print_wiring_instructions(gateway_id, lambda_arn)


if __name__ == "__main__":
    main()
