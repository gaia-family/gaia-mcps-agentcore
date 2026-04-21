"""
Gateway Request Interceptor

Sits between the Gateway and the Runtime. The Gateway has already validated the
inbound Cognito JWT. This function decodes it (no re-verification needed), extracts
the Cognito user ID (sub), and adds it as a custom header so the Runtime can
retrieve a user-scoped WorkloadAccessToken.

Header naming: AgentCore Runtime targets only pass through headers that start with
"X-Amzn-Bedrock-AgentCore-Runtime-Custom-" (SDK constant CUSTOM_HEADER_PREFIX).
The Gateway allowedRequestHeaders allowlist uses the same name. The SDK's
_build_request_context collects these into BedrockAgentCoreContext.get_request_headers().

Idempotent: stateless, no side effects — safe to retry.
"""

import base64
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verifying signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as e:
        logger.error("Failed to decode JWT payload: %s", e)
        return {}


def lambda_handler(event, context):
    logger.info("Event top-level keys: %s", list(event.keys()))

    mcp = event.get("mcp", {})
    gateway_request = mcp.get("gatewayRequest", {})
    inbound_headers = gateway_request.get("headers", {})
    body = gateway_request.get("body", {})

    logger.info("Inbound header keys: %s", list(inbound_headers.keys()))

    # Extract sub from the Cognito JWT (already validated by Gateway)
    auth = inbound_headers.get("Authorization") or inbound_headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()

    sub = None
    if token:
        claims = _decode_jwt_payload(token)
        sub = claims.get("sub")
        logger.info("Intercepted request for sub: %s", sub)  # sub is not sensitive
    else:
        logger.warning("No Authorization header found in request")

    # Inject using the AgentCore Runtime custom header prefix.
    # The Runtime invocation proxy only passes through headers starting with
    # "X-Amzn-Bedrock-AgentCore-Runtime-Custom-" — arbitrary custom headers
    # (like "X-User-Sub") are stripped before they reach the container.
    # This prefix is also the only one the SDK's _build_request_context collects
    # into BedrockAgentCoreContext.get_request_headers().
    # Do NOT pass through inbound headers — that risks including prohibited
    # x-amzn-* or x-forwarded-* headers which fail Gateway validation.
    CUSTOM_HEADER_PREFIX = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-"
    outbound_headers = {}
    if sub:
        outbound_headers[f"{CUSTOM_HEADER_PREFIX}User-Sub"] = sub

    logger.info("Outbound header keys: %s", list(outbound_headers.keys()))

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": outbound_headers,
                "body": body,
            }
        }
    }
