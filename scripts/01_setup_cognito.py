"""
Setup script: Creates a Cognito User Pool for AgentCore Gateway inbound JWT auth.

Usage:
    python scripts/01_setup_cognito.py

Outputs:
    resulting_config.json  - Cognito configuration used by subsequent scripts
"""

import boto3
import os

from config import save_result


def setup_cognito():
    region = os.environ["AWS_REGION"]
    pool_name = os.environ["COGNITO_POOL_NAME"]
    username = os.environ["COGNITO_TEST_USERNAME"]
    password = os.environ["COGNITO_TEST_PASSWORD"]
    temp_password = os.environ["COGNITO_TEST_TEMP_PASSWORD"]
    resource_server_id = os.environ["COGNITO_RESOURCE_SERVER_ID"]
    resource_server_name = os.environ["COGNITO_RESOURCE_SERVER_NAME"]

    cognito = boto3.client("cognito-idp", region_name=region)

    print("Creating Cognito User Pool...")
    pool = cognito.create_user_pool(
        PoolName=pool_name,
        Policies={"PasswordPolicy": {"MinimumLength": 8}},
    )
    pool_id = pool["UserPool"]["Id"]
    print(f"  Pool ID: {pool_id}")

    # Domain is required for the client_credentials (M2M) token endpoint
    domain_prefix = f"gateway-demo-{pool_id.split('_')[1].lower()}"
    print(f"Creating Cognito domain '{domain_prefix}'...")
    cognito.create_user_pool_domain(UserPoolId=pool_id, Domain=domain_prefix)
    cognito_domain = f"{domain_prefix}.auth.{region}.amazoncognito.com"
    token_endpoint = f"https://{cognito_domain}/oauth2/token"
    print(f"  Token endpoint: {token_endpoint}")

    # Resource server (required for client_credentials scopes)
    print("Creating resource server...")
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier=resource_server_id,
        Name=resource_server_name,
        Scopes=[{"ScopeName": "access", "ScopeDescription": "Gateway access"}],
    )

    print("Creating App Client (user-facing)...")
    user_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{pool_name}UserClient",
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    user_client_id = user_client["UserPoolClient"]["ClientId"]
    print(f"  User Client ID: {user_client_id}")

    # Agent client for authenticating with the gateway (client_credentials grant)
    print("Creating App Client (agent-facing, with client secret)...")
    agent_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{pool_name}AgentClient",
        GenerateSecret=True,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[f"{resource_server_id}/access"],
        AllowedOAuthFlowsUserPoolClient=True,
    )
    agent_client_id = agent_client["UserPoolClient"]["ClientId"]
    agent_client_secret = agent_client["UserPoolClient"]["ClientSecret"]
    print(f"  Agent Client ID: {agent_client_id}")

    print(f"Creating test user '{username}'...")
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=username,
        TemporaryPassword=temp_password,
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )

    print("Verifying user authentication...")
    auth = cognito.initiate_auth(
        ClientId=user_client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    _ = auth["AuthenticationResult"]["AccessToken"]

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
        "/.well-known/openid-configuration"
    )

    save_result({
        "cognito_pool_id": pool_id,
        "cognito_user_client_id": user_client_id,
        "cognito_agent_client_id": agent_client_id,
        "cognito_agent_client_secret": agent_client_secret,
        "cognito_discovery_url": discovery_url,
        "cognito_domain": cognito_domain,
        "cognito_username": username,
        "cognito_password": password,
    })

    print("\nCognito setup complete!")
    print("\nValues for script 02 (create gateway):")
    print(f"  --discovery-url    {discovery_url}")
    print(f"  --allowed-audience {pool_id}")
    print(f"  --allowed-clients  {user_client_id}")
    print(f"  --client-id        {agent_client_id}")
    print("  --client-secret    (saved to resulting_config.json)")
    print("\nConfiguration saved to resulting_config.json")


if __name__ == "__main__":
    setup_cognito()
