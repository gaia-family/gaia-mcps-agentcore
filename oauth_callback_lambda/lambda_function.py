"""
OAuth callback handler for AgentCore Identity USER_FEDERATION flow.

AgentCore redirects the user's browser here after HubSpot consent:
  GET /callback?session_id=<sessionUri>&user_sub=<userId>

We call CompleteResourceTokenAuth to bind the session to the user,
then AgentCore stores the HubSpot token in its vault.
"""

import json
import os
import boto3

REGION = os.environ.get("AWS_REGION")


def lambda_handler(event, context):
    print("FULL EVENT:", json.dumps(event))

    params = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    print("queryStringParameters:", json.dumps(params))
    print("pathParameters:", json.dumps(path_params))

    session_id = params.get("session_id")
    user_sub = params.get("state") or params.get("user_sub") or path_params.get("user_sub")

    if not session_id or not user_sub:
        return _html(400, "Missing session_id or user_sub parameter.")

    try:
        dp = boto3.client("bedrock-agentcore", region_name=REGION)
        dp.complete_resource_token_auth(
            sessionUri=session_id,
            userIdentifier={"userId": user_sub},
        )
        return _html(200, "Authorization complete! You can close this tab and return to the app.")
    except Exception as exc:
        return _html(500, f"Failed to complete authorization: {exc}")


def _html(status: int, message: str) -> dict:
    body = f"""<!DOCTYPE html>
<html><head><title>AgentCore OAuth Callback</title></head>
<body style="font-family:sans-serif;text-align:center;padding:4rem">
  <h2>{'✓' if status == 200 else '✗'} {message}</h2>
</body></html>"""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html"},
        "body": body,
    }
