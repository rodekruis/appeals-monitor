"""Load secrets from Azure Key Vault into environment variables.

When KEY_VAULT_URL is set, all secrets in the vault are fetched and injected as
environment variables (hyphens replaced with underscores, uppercased).  This
lets the rest of the code use plain ``os.getenv()`` regardless of whether
secrets come from a ``.env`` file or Key Vault.

Requires the container/host to have a managed identity with the
**Key Vault Secrets User** role on the vault.
"""

import os

from appeals_monitor.logger import logger

# Key Vault secret names use hyphens; env vars use underscores.
# e.g. "SENDGRID-API-KEY" -> "SENDGRID_API_KEY"


def load_secrets_from_key_vault() -> int:
    """Fetch all secrets from Key Vault and set them as env vars.

    Returns the number of secrets loaded.  Skips secrets that are already
    present in the environment so that explicit env vars (or .env) take
    precedence.
    """
    vault_url = os.getenv("KEY_VAULT_URL")
    if not vault_url:
        logger.debug("KEY_VAULT_URL not set — skipping Key Vault secret loading")
        return 0

    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)

    count = 0
    for secret_properties in client.list_properties_of_secrets():
        if not secret_properties.enabled:
            continue
        env_name = secret_properties.name.replace("-", "_").upper()
        if os.getenv(env_name):
            logger.debug(f"Skipping {env_name} — already set in environment")
            continue
        secret = client.get_secret(secret_properties.name)
        os.environ[env_name] = secret.value
        count += 1

    logger.info(f"Loaded {count} secret(s) from Key Vault")
    return count
