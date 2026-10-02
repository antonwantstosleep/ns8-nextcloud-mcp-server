#
# Copyright (C) 2026 Anton
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Switch module state to upstream multi_user_basic.

cbcoutinho/nextcloud-mcp-server v0.198.3 (config_validators.py):

- MCP_DEPLOYMENT_MODE=multi_user_basic is explicit. Without it, missing
  Nextcloud credentials fall through to login_flow, not pass-through Basic Auth.
- nextcloud_username and nextcloud_password are forbidden in that mode.
  If either is set, startup validation raises and the container never serves.
- single_user_basic (the previous default) embeds those credentials and
  serves /mcp with no client Authorization header.
- ENABLE_SEMANTIC_SEARCH in a multi-user mode turns background operations on.
  validate_configuration then requires TOKEN_ENCRYPTION_KEY and
  TOKEN_STORAGE_DB. The storage path is already in mcp.env; the Fernet key
  is generated here and kept in secrets.env. It is not a Nextcloud password.
"""

import base64
import os

import agent

_CREDENTIAL_ENV_KEYS = ("NEXTCLOUD_USERNAME", "NEXTCLOUD_PASSWORD")


def generate_fernet_key():
    """Return a Fernet key (urlsafe base64 of 32 random bytes)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def migrate_to_multi_user_basic(state_dir, *, create_secrets=False):
    """Drop stored Nextcloud credentials and pin multi_user_basic.

    mcp.env is rewritten only when it already exists, so an instance that
    has never been configured is left alone. secrets.env loses
    NEXTCLOUD_PASSWORD. When the file exists, or create_secrets is set,
    a TOKEN_ENCRYPTION_KEY is added if missing and then left unchanged.
    NEXTCLOUD_USERNAME and NEXTCLOUD_PASSWORD are removed from the agent
    environment file.
    """
    state_dir = state_dir or "."
    mcp_path = os.path.join(state_dir, "mcp.env")
    if os.path.exists(mcp_path):
        envmap = agent.read_envfile(mcp_path)
        for key in _CREDENTIAL_ENV_KEYS:
            envmap.pop(key, None)
        envmap["MCP_DEPLOYMENT_MODE"] = "multi_user_basic"
        agent.write_envfile(mcp_path, envmap)
        os.chmod(mcp_path, 0o600)

    secrets_path = os.path.join(state_dir, "secrets.env")
    secrets_exist = os.path.exists(secrets_path)
    if secrets_exist or create_secrets:
        secrets = agent.read_envfile(secrets_path) if secrets_exist else {}
        secrets.pop("NEXTCLOUD_PASSWORD", None)
        if not str(secrets.get("TOKEN_ENCRYPTION_KEY", "")).strip():
            secrets["TOKEN_ENCRYPTION_KEY"] = generate_fernet_key()
        agent.write_envfile(secrets_path, secrets)
        os.chmod(secrets_path, 0o600)

    for key in _CREDENTIAL_ENV_KEYS:
        agent.unset_env(key)
