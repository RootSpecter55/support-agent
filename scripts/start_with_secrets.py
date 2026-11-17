"""Container entrypoint. Fills in the environment, validates settings, then starts the API.

    SECRETS_SOURCE=local  load variables from .env (if present) without overriding real env vars
    SECRETS_SOURCE=aws    fetch one JSON secret (SECRET_ID) from AWS Secrets Manager and copy its
                          keys into the environment

Either way app.core.config validates every setting before the server starts, so the app behaves
the same whichever source the values came from.
"""

import json
import os
import sys
from collections.abc import MutableMapping
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

# Keys the AWS secret must contain. Non-sensitive settings come from the task definition.
REQUIRED_SECRET_KEYS = ("DATABASE_URL", "CLIENT_API_KEY", "STAFF_API_KEY")


class SecretsError(Exception):
    pass


def load_secrets(env: MutableMapping[str, str], client: Any = None) -> None:
    source = env.get("SECRETS_SOURCE", "local")

    if source == "local":
        for key, value in dotenv_values(".env").items():
            if value is not None:
                env.setdefault(key, value)
        return
    if source != "aws":
        raise SecretsError(f'SECRETS_SOURCE must be "local" or "aws", got "{source}"')

    secret_id = env.get("SECRET_ID")
    if not secret_id:
        raise SecretsError("SECRET_ID is required when SECRETS_SOURCE=aws")

    client = client or boto3.client("secretsmanager", region_name=env.get("AWS_REGION"))
    try:
        secret_string = client.get_secret_value(SecretId=secret_id).get("SecretString")
    except (ClientError, BotoCoreError) as exc:
        raise SecretsError(f'Could not read secret "{secret_id}": {exc}') from exc
    if not secret_string:
        raise SecretsError(f'Secret "{secret_id}" has no string value')

    try:
        values = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise SecretsError(f'Secret "{secret_id}" is not valid JSON') from exc
    if not isinstance(values, dict):
        raise SecretsError(f'Secret "{secret_id}" must be a JSON object of key/value pairs')

    missing = [key for key in REQUIRED_SECRET_KEYS if not values.get(key)]
    if missing:
        raise SecretsError(f'Secret "{secret_id}" is missing required keys: {", ".join(missing)}')
    for key, value in values.items():
        env[key] = str(value)


def main() -> None:
    try:
        load_secrets(os.environ)
    except SecretsError as exc:
        print(f"[start_with_secrets] {exc}", file=sys.stderr)
        sys.exit(1)

    # Imported only now, after the environment is complete.
    import uvicorn

    from app.core.config import get_settings

    try:
        get_settings()
    except Exception as exc:
        print(f"[start_with_secrets] Invalid configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
