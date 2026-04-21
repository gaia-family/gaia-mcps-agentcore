"""
Adds Google as a federated social IdP to the existing Cognito User Pool
and enables the authorization_code OAuth flow on the user-facing app client.

The Gateway's customJwtAuthorizer is unchanged — it still validates Cognito
tokens. Cognito handles the Google OAuth dance and issues its own JWTs.

Prerequisites:
  - Google OAuth 2.0 Web App credential created in Google Cloud Console
  - Authorized redirect URI set to:
    https://<cognito_domain>/oauth2/idpresponse
    (cognito_domain is saved to resulting_config.json by script 01)

Usage:
    python scripts/09_setup_google_idp.py --client-id <id> --client-secret <secret> --callback-url <url>

    --callback-url  The OAuth callback URL Claude.ai (or your MCP client) uses.
                    Defaults to https://claude.ai/oauth/callback if not provided.
"""

import argparse
import boto3
import os
import sys

from config import load_result

REGION = os.environ["AWS_REGION"]


def add_google_idp(cognito, pool_id: str, google_client_id: str, google_client_secret: str):
    try:
        cognito.describe_identity_provider(
            UserPoolId=pool_id,
            ProviderName="Google",
        )
        print(f"  Google IdP already exists — updating...")
        cognito.update_identity_provider(
            UserPoolId=pool_id,
            ProviderName="Google",
            ProviderDetails={
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "authorize_scopes": "email profile openid",
            },
            AttributeMapping={
                "email": "email",
                "name": "name",
                "username": "sub",
            },
        )
        print("  Updated.")
    except cognito.exceptions.ResourceNotFoundException:
        print(f"  Creating Google IdP...")
        cognito.create_identity_provider(
            UserPoolId=pool_id,
            ProviderName="Google",
            ProviderType="Google",
            ProviderDetails={
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "authorize_scopes": "email profile openid",
            },
            AttributeMapping={
                "email": "email",
                "name": "name",
                "username": "sub",
            },
        )
        print("  Created.")


def update_app_client(cognito, pool_id: str, user_client_id: str, callback_url: str):
    current = cognito.describe_user_pool_client(
        UserPoolId=pool_id,
        ClientId=user_client_id,
    )["UserPoolClient"]

    existing_callbacks = current.get("CallbackURLs", [])
    if callback_url not in existing_callbacks:
        existing_callbacks.append(callback_url)

    cognito.update_user_pool_client(
        UserPoolId=pool_id,
        ClientId=user_client_id,
        AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=["email", "openid", "profile"],
        AllowedOAuthFlowsUserPoolClient=True,
        CallbackURLs=existing_callbacks,
        LogoutURLs=current.get("LogoutURLs", []),
        SupportedIdentityProviders=["Google", "COGNITO"],
        ExplicitAuthFlows=current.get("ExplicitAuthFlows", [
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ]),
    )
    print(f"  App client updated. CallbackURLs: {existing_callbacks}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True, help="Google OAuth 2.0 client ID")
    parser.add_argument("--client-secret", required=True, help="Google OAuth 2.0 client secret")
    parser.add_argument(
        "--callback-url",
        default="https://claude.ai/oauth/callback",
        help="OAuth callback URL for your MCP client (default: https://claude.ai/oauth/callback)",
    )
    args = parser.parse_args()

    cfg = load_result()
    if "cognito_pool_id" not in cfg:
        print("ERROR: cognito_pool_id not in resulting_config.json. Run script 01 first.")
        sys.exit(1)

    pool_id = cfg["cognito_pool_id"]
    user_client_id = cfg["cognito_user_client_id"]
    cognito_domain = cfg["cognito_domain"]

    cognito = boto3.client("cognito-idp", region_name=REGION)

    print(f"Pool:        {pool_id}")
    print(f"User client: {user_client_id}")
    print(f"Region:      {REGION}")

    print("\nStep 1: Google IdP")
    add_google_idp(cognito, pool_id, args.client_id, args.client_secret)

    print("\nStep 2: App client OAuth config")
    update_app_client(cognito, pool_id, user_client_id, args.callback_url)

    print("\nDone. Hosted UI login URL for manual testing:")
    print(
        f"  https://{cognito_domain}/login"
        f"?client_id={user_client_id}"
        f"&response_type=code"
        f"&scope=email+openid+profile"
        f"&redirect_uri={args.callback_url}"
    )


if __name__ == "__main__":
    main()
