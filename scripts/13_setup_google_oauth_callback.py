"""
Wires the existing OAuth callback endpoint to the Google Sheets workload identity.

The callback Lambda is already deployed by script 08. This script:
  1. Reads the existing callback URL from resulting_config.json
  2. Registers it on the Google Sheets workload identity
  3. Sets OAUTH_CALLBACK_URL env var on the Google Sheets Lambda

Reads from .env:   AWS_REGION, GOOGLE_SHEETS_WORKLOAD_IDENTITY_NAME, GOOGLE_SHEETS_LAMBDA_NAME
Reads from resulting_config.json: oauth_callback_url

Usage:
    python scripts/13_setup_google_oauth_callback.py
"""

import boto3
import os

import config
from config import load_result

REGION = os.environ["AWS_REGION"]
WORKLOAD_IDENTITY_NAME = os.environ["GOOGLE_SHEETS_WORKLOAD_IDENTITY_NAME"]
LAMBDA_NAME = os.environ["GOOGLE_SHEETS_LAMBDA_NAME"]


def main():
    cfg = load_result()
    callback_url = cfg.get("oauth_callback_url")
    if not callback_url:
        print("ERROR: oauth_callback_url not found in resulting_config.json. Run script 08 first.")
        raise SystemExit(1)

    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)

    print(f"Registering callback URL on workload identity '{WORKLOAD_IDENTITY_NAME}'...")
    print(f"  URL: {callback_url}")
    ctrl.update_workload_identity(
        name=WORKLOAD_IDENTITY_NAME,
        allowedResourceOauth2ReturnUrls=[callback_url],
    )
    print("  Done.")

    print(f"\nSetting OAUTH_CALLBACK_URL on Lambda '{LAMBDA_NAME}'...")
    fn_cfg = lam.get_function_configuration(FunctionName=LAMBDA_NAME)
    env = fn_cfg.get("Environment", {}).get("Variables", {})
    env["OAUTH_CALLBACK_URL"] = callback_url
    lam.update_function_configuration(
        FunctionName=LAMBDA_NAME,
        Environment={"Variables": env},
    )
    lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
    print(f"  Set OAUTH_CALLBACK_URL={callback_url}")

    print(f"\nDone.")
    print(f"\nReady. On first tool call, visit the authorizationUrl to grant Google access.")


if __name__ == "__main__":
    main()
