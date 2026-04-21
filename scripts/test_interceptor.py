"""
Local test for the Gateway interceptor Lambda.
No AWS, no Docker, no dependencies — just Python.

Usage:
    python scripts/test_interceptor.py
"""

import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "interceptor"))
from lambda_function import lambda_handler


def make_fake_jwt(sub: str, extra: dict = None) -> str:
    """Build a minimal JWT with a known sub (signature is fake — that's fine,
    the interceptor never verifies it, mirroring Gateway behaviour)."""
    claims = {"sub": sub, "email": f"{sub}@example.com"}
    if extra:
        claims.update(extra)
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def run(label: str, event: dict) -> None:
    print(f"\n--- {label} ---")
    result = lambda_handler(event, {})
    print(json.dumps(result, indent=2))
    headers = result["mcp"]["transformedGatewayRequest"]["headers"]
    assert "authorization" not in {k.lower() for k in headers}, \
        "Authorization header should be stripped"
    print(f"  X-User-Sub: {headers.get('X-User-Sub', '(not set)')}")


# Case 1: normal request with a valid-looking JWT
run("With Authorization header", {
    "mcp": {
        "gatewayRequest": {
            "headers": {
                "Authorization": f"Bearer {make_fake_jwt('user-abc123')}",
                "Content-Type": "application/json",
                "X-Request-Id": "req-1",
            },
            "body": {"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        }
    }
})

# Case 2: no Authorization header (e.g. health check)
run("Without Authorization header", {
    "mcp": {
        "gatewayRequest": {
            "headers": {
                "Content-Type": "application/json",
            },
            "body": {},
        }
    }
})

# Case 3: malformed token (should not crash, just omit X-User-Sub)
run("Malformed token", {
    "mcp": {
        "gatewayRequest": {
            "headers": {"Authorization": "Bearer not.a.jwt"},
            "body": {},
        }
    }
})

print("\nAll cases passed.")
