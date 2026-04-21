import os
import boto3

REGION = os.environ.get("AWS_REGION")
WORKLOAD_IDENTITY_NAME = os.environ["WORKLOAD_IDENTITY_NAME"]
CREDENTIAL_PROVIDER_NAME = os.environ["CREDENTIAL_PROVIDER_NAME"]
CALLBACK_URL = os.environ.get("OAUTH_CALLBACK_URL", "")


class AuthRequiredError(Exception):
    def __init__(self, auth_url: str):
        self.auth_url = auth_url


def get_hubspot_access_token(user_sub: str) -> str:
    """Returns a HubSpot OAuth token for the given user.

    Raises AuthRequiredError if the user hasn't completed OAuth consent yet.
    """
    if not user_sub:
        raise RuntimeError("user_sub not available — custom header was not received.")

    dp = boto3.client("bedrock-agentcore", region_name=REGION)

    workload_resp = dp.get_workload_access_token_for_user_id(
        workloadName=WORKLOAD_IDENTITY_NAME,
        userId=user_sub,
    )
    workload_token = workload_resp["workloadAccessToken"]

    req = {
        "resourceCredentialProviderName": CREDENTIAL_PROVIDER_NAME,
        "workloadIdentityToken": workload_token,
        "oauth2Flow": "USER_FEDERATION",
        "scopes": ["crm.objects.contacts.read"],
        "customState": user_sub,
    }
    if CALLBACK_URL:
        req["resourceOauth2ReturnUrl"] = CALLBACK_URL

    resp = dp.get_resource_oauth2_token(**req)

    if "accessToken" in resp:
        return resp["accessToken"]

    if "authorizationUrl" in resp:
        raise AuthRequiredError(resp["authorizationUrl"])

    raise RuntimeError(f"Unexpected response from AgentCore Identity: {resp}")
